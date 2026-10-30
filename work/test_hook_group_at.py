import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent / "WechatHook4.1.10.27-fixed"


@unittest.skipUnless(ROOT.exists(), "WeChat Hook source is a local-only checkout")
class HookGroupAtContractTests(unittest.TestCase):
    def test_nosrc_route_forwards_atlist_to_native_text_sender(self):
        route = (ROOT / "src" / "SendTextMsg.cpp").read_text(encoding="utf-8")
        header = (ROOT / "include" / "wx_send.h").read_text(encoding="utf-8")
        sender = (ROOT / "src" / "wx_send.cpp").read_text(encoding="utf-8")

        self.assertIn('svr.Post("/SendTextMsg_NoSrc"', route)
        self.assertIn('reqJson.value("atlist", "")', route)
        self.assertIn('SendText(wxidorgid, msg, atlist)', route)
        self.assertIn('const std::string& atlist', header)
        self.assertIn('SetWeixinString(&msg->atlist, atlist)', sender)


if __name__ == "__main__":
    unittest.main()
