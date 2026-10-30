#!/usr/bin/env python3
"""人格一致性检查：读最近的回复，把「出戏」的挑出来。

移植自 chennianlaobai/qq-ai-bot 的 tools/check_persona.py（MIT License,
Copyright (c) 2026 chennianlaobai，许可全文见同目录 LICENSE-check_persona.txt）。
改动：去掉 NoneBot 配置依赖；读微信 Bot 的 work/wechat_ai_bridge_state/replies.jsonl；
按会话 id（--chat）和群聊/私聊（--kind）筛选；增加微信表情代码数量上限。

规则在同目录 `persona_rules.json`（人设本身在 `persona.md`）。只离线读日志，不影响 Bot 运行。

用法
----
    python check_persona.py                      # 检查最近 50 条
    python check_persona.py --last 200           # 多看点
    python check_persona.py --kind group         # 只看群聊
    python check_persona.py --chat <会话id>      # 只看某个会话（日志里的 chat 字段）
    python check_persona.py --text "您好，请问有什么我可以帮您？"   # 单句速测
    python check_persona.py --json               # 给别的程序接着处理
    python check_persona.py --strict             # 有 WARN 也算失败

退出码：默认命中 ERROR 级返回 1；`--strict` 时 WARN 也算失败。
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from collections import deque
from dataclasses import dataclass, asdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
RULES_PATH = HERE / "persona_rules.json"
LOG_PATH = HERE.parent.parent / "work" / "wechat_ai_bridge_state" / "replies.jsonl"
WECHAT_EMOJI_RE = re.compile(r"\[[\u4e00-\u9fff]{1,4}\]")

KAOMOJI_WORDS = ("QAQ", "TAT", "T_T", "orz", "OTZ", "OvO", "owo", "QwQ", "qwq", "owo")
KAOMOJI_RE = re.compile(r"[（(][^\u4e00-\u9fff\w]{1,14}[)）]")
SENT_SPLIT_RE = re.compile(r"[。！？!?…\n]+")


@dataclass
class Finding:
    rule_id: str
    level: str
    desc: str
    hint: str
    evidence: str = ""


@dataclass
class Result:
    entry: dict
    findings: list[Finding]
    score: int


def _compile_all(policy: dict, key: str) -> list[tuple[dict, re.Pattern]]:
    compiled = []
    for r in policy.get(key, []):
        if r.get("level", "").lower() == "off":
            continue
        try:
            compiled.append((r, re.compile(r["pattern"])))
        except re.error as e:
            print(f"⚠️  规则 {r.get('id')} 的正则写错了，跳过：{e}", file=sys.stderr)
    return compiled


def _rules(policy: dict) -> list[tuple[dict, re.Pattern]]:
    return _compile_all(policy, "rules")


def _scene_rules(policy: dict) -> list[dict]:
    """场景规则要三个正则（触发 / 祝福词 / 不正经词），单独编译。"""
    out = []
    for r in policy.get("scene_rules", []):
        if str(r.get("level", "")).lower() == "off":
            continue
        try:
            out.append({
                "rule": r,
                "trigger": re.compile(r["trigger"]),
                "bless": re.compile(r["bless"]),
                "rough": re.compile(r["rough"]),
            })
        except (KeyError, re.error) as e:
            print(f"⚠️  场景规则 {r.get('id')} 配置有问题，跳过：{e}", file=sys.stderr)
    return out


def check_scene(user_msg: str, reply: str, scenes: list[dict]) -> list[Finding]:
    """场景规则：既看用户问了什么，也看它怎么答的。

    目前一条 R15：被求祝福时，**开头第一句**必须干净 —— 不能同一句里既有祝福词
    又有插科打诨的词。正经祝福和不正经的话要分成两句（换行/句号），第二句往后
    随便皮，不查。所以「祝你生日快乐，可惜你又老了一岁绷不住了」会被标出来，
    而「祝你生日快乐\\n可惜你又老了一岁，绷不住了」通过。
    """
    out: list[Finding] = []
    reply = (reply or "").strip()
    if not reply or not user_msg:
        return out
    head = next((s.strip() for s in SENT_SPLIT_RE.split(reply) if s.strip()), "")
    for sc in scenes:
        rule, level = sc["rule"], sc["rule"].get("level", "warn")
        if not sc["trigger"].search(user_msg):
            continue
        if head and sc["bless"].search(head) and sc["rough"].search(head):
            out.append(Finding(rule.get("id", "?"), level, rule.get("desc", ""),
                               rule.get("hint", ""), head[:40]))
    return out


def apply_density(entries: list[dict], results: list[Result], policy: dict) -> None:
    """密度类规则：允许出现，只在最近的窗口里占比过高时才提示。

    以每条命中 i 回溯一个 window 大小的窗口，若窗口内命中数 >= min_hits 且占比 >
    max_ratio，或者连着出现超过 max_consecutive 句，就把超出的那些标出来。所以
    孤零零一次卖萌不会中招，只有「句句都卖萌」的段落会被归责。
    """
    for rule, pattern in _compile_all(policy, "density_rules"):
        window = int(rule.get("window", 10))
        min_hits = int(rule.get("min_hits", 3))
        max_ratio = float(rule.get("max_ratio", 0.5))
        max_run = int(rule.get("max_consecutive", 3))
        level = rule.get("level", "warn")
        hits = [i for i, e in enumerate(entries)
                if (e.get("reply") or "").strip() and pattern.search(e["reply"])]

        flagged: set[int] = set()

        # a) 最近 window 条里占比过高
        for i in hits:
            lo = max(0, i - window + 1)
            n = sum(1 for j in hits if lo <= j <= i)
            if n >= min_hits and n / (i - lo + 1) > max_ratio:
                flagged.add(i)

        # b) 连着好几句都在卖萌（超过允许的第 max_consecutive 句起算）
        start = 0
        for k in range(1, len(hits) + 1):
            if k == len(hits) or hits[k] - hits[k - 1] != 1:
                seg = hits[start:k]
                if len(seg) > max_run:
                    flagged.update(seg[max_run:])
                start = k

        for i in sorted(flagged):
            results[i].findings.append(Finding(
                rule.get("id", "?"), level, rule.get("desc", ""), rule.get("hint", ""), ""))
            results[i].score = score_of(results[i].findings, policy.get("penalty", {}))


def _count_kaomoji(text: str) -> int:
    return len(KAOMOJI_RE.findall(text)) + sum(
        text.upper().count(w.upper()) for w in KAOMOJI_WORDS
    )


def _sentences(text: str) -> int:
    return len([s for s in SENT_SPLIT_RE.split(text) if s.strip()])


def check_one(text: str, policy: dict, compiled, prev: list[str] | None = None) -> list[Finding]:
    """对一条回复套全部规则。"""
    limits = policy.get("limits", {})
    out: list[Finding] = []

    text = (text or "").strip()
    if not text:
        return [Finding("R0-empty", "warn", "空回复（模型没吐出东西）",
                        "看同一时间的日志，多半是调用失败", "")]

    # 1) 正则类规则
    for rule, pattern in compiled:
        level = rule.get("level", "warn")
        m = pattern.search(text)
        if m:
            out.append(Finding(
                rule.get("id", "?"), level, rule.get("desc", ""),
                rule.get("hint", ""), m.group(0)[:30],
            ))

    # 2) 长度 / 结构类
    if len(text) > limits.get("max_chars_hard", 120):
        out.append(Finding("R10-too-long", "error",
                           f"太长：{len(text)} 字（硬上限 {limits.get('max_chars_hard')}）",
                           "人设：闲聊大多一两句；认真问题可以长，确认一下是不是", text[:30] + "…"))
    elif len(text) > limits.get("max_chars", 60):
        out.append(Finding("R10-too-long", "warn",
                           f"偏长：{len(text)} 字（软上限 {limits.get('max_chars')}）",
                           "能砍一半就砍", text[:30] + "…"))

    n_sent = _sentences(text)
    if n_sent > limits.get("max_sentences", 2):
        out.append(Finding("R11-too-many-sentences", "warn",
                           f"说了 {n_sent} 句（软上限 {limits.get('max_sentences', 2)}）", "闲聊一两句就够，认真问题除外", ""))

    n_emoji = len(WECHAT_EMOJI_RE.findall(text))
    if n_emoji > limits.get("max_wechat_emoji", 1):
        out.append(Finding("R16-wechat-emoji", "warn", f"微信表情代码 {n_emoji} 个",
                           "人设：一条最多一个，很多轮都不用也正常", ""))

    n_kao = _count_kaomoji(text)
    if n_kao > limits.get("max_kaomoji", 2):
        out.append(Finding("R12-kaomoji", "warn", f"颜文字 {n_kao} 个，卖萌过头了",
                           "偶尔一个就行", ""))

    # 3) 复读：跟最近几条里的任何一条太像都算（会跟着别人复读也很出戏）
    if prev:
        threshold = limits.get("max_dedup_similarity", 0.75)
        worst, worst_text = 0.0, ""
        for old in prev:
            ratio = difflib.SequenceMatcher(None, text, old).ratio()
            if ratio > worst:
                worst, worst_text = ratio, old
        if worst >= threshold:
            out.append(Finding("R13-repeat", "warn",
                               f"和最近的一句重复度 {worst:.0%}", "换个说法，别复读",
                               worst_text[:30]))

    return out


def score_of(findings: list[Finding], penalty: dict) -> int:
    s = 100
    for f in findings:
        s -= penalty.get(f.level, 10 if f.level == "warn" else 40)
    return max(0, s)


def read_jsonl(path: Path, last: int = 50, chat: str | None = None,
               kind: str | None = None) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if chat:
        rows = [r for r in rows if r.get("chat") == chat]
    if kind:
        rows = [r for r in rows if r.get("kind") == kind]
    return rows[-last:]


def analyze(entries: list[dict], policy: dict) -> list[Result]:
    compiled = _rules(policy)
    scenes = _scene_rules(policy)
    penalty = policy.get("penalty", {})
    results: list[Result] = []
    recent: deque = deque(maxlen=5)  # 最近 5 条回复，用于查复读
    for e in entries:
        findings = check_one(e.get("reply", ""), policy, compiled, list(recent))
        findings += check_scene(e.get("user_msg", ""), e.get("reply", ""), scenes)
        if e.get("error"):
            findings.append(Finding("R9-system-error", "error", "这一轮调用报错了",
                                    "看 bridge.log，不是人设问题但要单独盯", str(e["error"])[:60]))
        results.append(Result(e, findings, score_of(findings, penalty)))
        t = (e.get("reply") or "").strip()
        if t:
            recent.append(t)
    apply_density(entries, results, policy)
    return results


def render(results: list[Result], policy: dict, showed=40) -> None:
    if not results:
        print("没有可检查的记录。先让 Bot 聊几句，或先用 --text 速测。")
        return
    total = len(results)
    clean = [r for r in results if not r.findings]
    errors = [(r, f) for r in results for f in r.findings if f.level == "error"]
    warns = [(r, f) for r in results for f in r.findings if f.level == "warn"]
    avg = sum(r.score for r in results) // total

    print(f"检查 {total} 条回复　人设贴合度均分 {avg}　"
          f"干净 {len(clean)} 条（{len(clean) / total:.0%}）")
    print(f"命中：ERROR {len(errors)} 处 / WARN {len(warns)} 处\n")

    counter: dict[str, int] = {}
    for r in results:
        for f in r.findings:
            counter[f.rule_id] = counter.get(f.rule_id, 0) + 1
    if counter:
        print("问题分布：")
        for rid, n in sorted(counter.items(), key=lambda kv: -kv[1]):
            desc = next((f.desc for r in results for f in r.findings if f.rule_id == rid), "")
            print(f"  · {rid} × {n} —— {desc}")
        top_id = max(counter, key=counter.get)
        top_desc = next(f.hint for r in results for f in r.findings if f.rule_id == top_id)
        print(f"\n最该改的一条：{top_id} → {top_desc}")
        print("（建议在 persona.md 里把这条写得更明确，或改 persona_rules.json 放宽）\n")

    print("明细：")
    shown = 0
    for r in results:
        if not r.findings:
            continue
        e = r.entry
        kind = "群聊" if e.get("kind") == "group" else "私聊"
        print(f"  [{e.get('ts', '')}] {kind} · 会话 {e.get('chat', '?')}")
        if e.get("user_msg"):
            print(f"    触发：{e['user_msg'][:40]}")
        reply = (e.get("reply") or "").replace("\n", " / ")
        print(f"    回复：{reply[:80]}{'…' if len(reply) > 80 else ''}")
        for f in r.findings:
            icon = "❌" if f.level == "error" else "⚠️ "
            ev = f" 证据「{f.evidence}」" if f.evidence else ""
            print(f"    {icon} {f.rule_id} {f.desc}{ev}")
        shown += 1
        if shown >= showed:
            print(f"  …还有 {sum(1 for r in results if r.findings) - shown} 条，加 --last 或先看最上面的分布")
            break


def main() -> int:
    ap = argparse.ArgumentParser(description="人格一致性检查")
    ap.add_argument("--last", type=int, default=50, help="看最近多少条（默认 50）")
    ap.add_argument("--chat", default=None, help="只看某个会话（日志里的 chat 字段）")
    ap.add_argument("--kind", default=None, choices=["group", "private"], help="只看群聊或私聊")
    ap.add_argument("--text", default=None, help="单句速测，不读日志")
    ap.add_argument("--ask", default=None,
                    help="配合 --text：假装用户说了这句，把场景规则也一起测（例：--ask '祝我生日快乐'）")
    ap.add_argument("--file", default=None, help="读指定的 jsonl（默认读 Bot 的 replies.jsonl）")
    ap.add_argument("--rules", default=None, help="规则文件（默认同目录 persona_rules.json）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--strict", action="store_true", help="WARN 也算失败")
    args = ap.parse_args()

    try:
        policy = json.loads(Path(args.rules or RULES_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"❌ 规则文件加载失败：{e}", file=sys.stderr)
        return 1
    log_path = Path(args.file) if args.file else LOG_PATH

    compiled = _rules(policy)
    penalty = policy.get("penalty", {})

    if args.text:
        findings = check_one(args.text, policy, compiled)
        if args.ask:
            findings += check_scene(args.ask, args.text, _scene_rules(policy))
        score = score_of(findings, penalty)
        if args.json:
            print(json.dumps({"text": args.text, "score": score,
                              "findings": [asdict(f) for f in findings]},
                             ensure_ascii=False, indent=2))
        else:
            print(f"得分 {score}／100")
            if not findings:
                print("✅ 没问题，这句是群友味儿的")
            for f in findings:
                icon = "❌" if f.level == "error" else "⚠️ "
                print(f"{icon} {f.rule_id} {f.desc}｜{f.hint}")
        return 1 if (findings if args.strict else [f for f in findings if f.level == "error"]) else 0

    entries = read_jsonl(log_path, last=args.last, chat=args.chat, kind=args.kind)
    if not entries:
        print(f"没有读到任何记录：{log_path}\n"
              "（Bot 成功发出回复后才会写入；确认 config.json 里 reply_log_enabled 为 true）")
        return 0

    results = analyze(entries, policy)

    if args.json:
        print(json.dumps([{"entry": r.entry, "score": r.score,
                           "findings": [asdict(f) for f in r.findings]} for r in results],
                         ensure_ascii=False, indent=2))
    else:
        render(results, policy)

    hit_error = any(f.level == "error" for r in results for f in r.findings)
    hit_warn = any(f.level == "warn" for r in results for f in r.findings)
    return 1 if (hit_error or (args.strict and hit_warn)) else 0


if __name__ == "__main__":
    sys.exit(main())
