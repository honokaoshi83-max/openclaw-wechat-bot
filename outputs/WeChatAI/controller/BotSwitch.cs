using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows.Forms;

internal sealed class BotStatus
{
    public bool Disabled;
    public bool Hook;
    public bool Bridge;
    public bool OpenClaw;
    public string[] Errors = new string[0];

    public bool AnyActive { get { return Bridge || OpenClaw; } }

    public static BotStatus FromJson(string json)
    {
        JavaScriptSerializer serializer = new JavaScriptSerializer();
        Dictionary<string, object> values = serializer.DeserializeObject(json) as Dictionary<string, object>;
        if (values == null) throw new InvalidDataException("Status response is not a JSON object.");
        BotStatus result = new BotStatus();
        result.Disabled = ReadBool(values, "disabled");
        result.Hook = ReadBool(values, "hook");
        result.Bridge = ReadBool(values, "bridge");
        result.OpenClaw = ReadBool(values, "openclaw");
        object errors;
        if (values.TryGetValue("errors", out errors) && errors is object[])
        {
            object[] items = (object[])errors;
            List<string> messages = new List<string>();
            foreach (object item in items) if (item != null) messages.Add(Convert.ToString(item));
            result.Errors = messages.ToArray();
        }
        return result;
    }

    private static bool ReadBool(Dictionary<string, object> values, string name)
    {
        object value;
        return values.TryGetValue(name, out value) && value != null && Convert.ToBoolean(value);
    }
}

internal sealed class BotSwitchForm : Form
{
    private readonly string managerPath;
    private readonly Label overallStatus;
    private readonly Label hookStatus;
    private readonly Label bridgeStatus;
    private readonly Label openClawStatus;
    private readonly Label detailStatus;
    private readonly Button toggleButton;
    private readonly Timer refreshTimer;
    private BotStatus current;
    private bool busy;

    public BotSwitchForm()
    {
        managerPath = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "manage_bot_stack.ps1");
        Text = "大肥鱼控制台";
        StartPosition = FormStartPosition.CenterScreen;
        ClientSize = new Size(470, 390);
        MinimumSize = new Size(470, 390);
        MaximumSize = new Size(470, 390);
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = false;
        Font = new Font("Microsoft YaHei UI", 10.0f);
        BackColor = Color.FromArgb(248, 250, 252);

        Label title = new Label();
        title.Text = "微信 AI 机器人";
        title.Font = new Font("Microsoft YaHei UI", 19.0f, FontStyle.Bold);
        title.SetBounds(24, 18, 410, 38);
        Controls.Add(title);

        overallStatus = new Label();
        overallStatus.Text = "正在读取运行状态…";
        overallStatus.Font = new Font("Microsoft YaHei UI", 11.0f);
        overallStatus.ForeColor = Color.FromArgb(80, 90, 105);
        overallStatus.SetBounds(26, 62, 415, 28);
        Controls.Add(overallStatus);

        toggleButton = new Button();
        toggleButton.Text = "读取中…";
        toggleButton.SetBounds(25, 100, 415, 58);
        toggleButton.Font = new Font("Microsoft YaHei UI", 14.0f, FontStyle.Bold);
        toggleButton.FlatStyle = FlatStyle.Flat;
        toggleButton.FlatAppearance.BorderSize = 0;
        toggleButton.BackColor = Color.FromArgb(30, 160, 105);
        toggleButton.ForeColor = Color.White;
        toggleButton.Enabled = false;
        toggleButton.Click += ToggleClicked;
        Controls.Add(toggleButton);

        Label section = new Label();
        section.Text = "组件状态";
        section.Font = new Font("Microsoft YaHei UI", 10.0f, FontStyle.Bold);
        section.SetBounds(27, 177, 130, 24);
        Controls.Add(section);

        hookStatus = AddStatusRow("微信 Hook", 207);
        bridgeStatus = AddStatusRow("消息桥接", 235);
        openClawStatus = AddStatusRow("OpenClaw / DeepSeek", 263);

        detailStatus = new Label();
        detailStatus.ForeColor = Color.FromArgb(150, 65, 55);
        detailStatus.Font = new Font("Microsoft YaHei UI", 8.5f);
        detailStatus.AutoEllipsis = true;
        detailStatus.SetBounds(27, 349, 410, 20);
        Controls.Add(detailStatus);

        Label footer = new Label();
        footer.Text = "微信仍需手动登录；关闭此窗口不会停止机器人。";
        footer.ForeColor = Color.Gray;
        footer.Font = new Font("Microsoft YaHei UI", 8.0f);
        footer.SetBounds(27, 367, 415, 18);
        Controls.Add(footer);

        refreshTimer = new Timer();
        refreshTimer.Interval = 4000;
        refreshTimer.Tick += delegate { if (!busy) RunManager("Status"); };
        Shown += delegate { RunManager("Status"); refreshTimer.Start(); };
        FormClosed += delegate { refreshTimer.Stop(); };
    }

    private Label AddStatusRow(string caption, int top)
    {
        Label left = new Label();
        left.Text = caption;
        left.SetBounds(30, top, 160, 24);
        Controls.Add(left);
        Label right = new Label();
        right.Text = "检查中";
        right.TextAlign = ContentAlignment.MiddleRight;
        right.SetBounds(205, top, 225, 24);
        Controls.Add(right);
        return right;
    }

    private void ToggleClicked(object sender, EventArgs e)
    {
        if (busy) return;
        RunManager(current != null && !current.Disabled && current.AnyActive ? "Stop" : "Start");
    }

    private void RunManager(string action)
    {
        if (busy) return;
        busy = true;
        if (action != "Status")
        {
            toggleButton.Enabled = false;
            toggleButton.Text = action == "Stop" ? "正在关闭…" : "正在启动…";
            overallStatus.Text = action == "Stop" ? "正在停止机器人后台服务…" : "正在启动机器人后台服务…";
        }

        System.ComponentModel.BackgroundWorker worker = new System.ComponentModel.BackgroundWorker();
        worker.DoWork += delegate(object sender, System.ComponentModel.DoWorkEventArgs args)
        {
            args.Result = InvokeManager(action);
        };
        worker.RunWorkerCompleted += delegate(object sender, System.ComponentModel.RunWorkerCompletedEventArgs args)
        {
            busy = false;
            if (args.Error != null)
            {
                overallStatus.Text = "控制程序出错";
                detailStatus.Text = args.Error.GetBaseException().Message;
                toggleButton.Enabled = current != null;
                return;
            }
            ApplyStatus((BotStatus)args.Result);
        };
        worker.RunWorkerAsync();
    }

    private BotStatus InvokeManager(string action)
    {
        if (!File.Exists(managerPath)) throw new FileNotFoundException("缺少后台管理脚本", managerPath);
        string powershell = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows),
            "System32", "WindowsPowerShell", "v1.0", "powershell.exe");
        ProcessStartInfo info = new ProcessStartInfo();
        info.FileName = powershell;
        info.Arguments = "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File \"" +
            managerPath + "\" -Action " + action;
        info.WorkingDirectory = AppDomain.CurrentDomain.BaseDirectory;
        info.UseShellExecute = false;
        info.CreateNoWindow = true;
        info.WindowStyle = ProcessWindowStyle.Hidden;
        info.RedirectStandardOutput = true;
        info.RedirectStandardError = true;
        info.StandardOutputEncoding = Encoding.UTF8;
        info.StandardErrorEncoding = Encoding.UTF8;

        using (Process process = Process.Start(info))
        {
            string output = process.StandardOutput.ReadToEnd();
            string error = process.StandardError.ReadToEnd();
            if (!process.WaitForExit(180000))
            {
                try { process.Kill(); } catch { }
                throw new TimeoutException("后台操作超过 3 分钟，请查看启动日志。\n" + error);
            }
            if (process.ExitCode != 0)
            {
                string reason = String.IsNullOrWhiteSpace(error) ? output : error;
                throw new InvalidOperationException(String.IsNullOrWhiteSpace(reason) ?
                    "后台管理脚本执行失败。" : reason.Trim());
            }
            int jsonStart = output.IndexOf('{');
            if (jsonStart < 0) throw new InvalidDataException("后台没有返回状态数据。\n" + error);
            return BotStatus.FromJson(output.Substring(jsonStart).Trim());
        }
    }

    private void ApplyStatus(BotStatus status)
    {
        current = status;
        hookStatus.Text = status.Hook ? "在线" : "等待微信登录";
        bridgeStatus.Text = status.Bridge ? "运行中" : (status.Hook ? "启动中" : "待命");
        openClawStatus.Text = status.OpenClaw ? "运行中" : "已停止";
        if (status.Disabled)
            overallStatus.Text = status.AnyActive ? "已发出关闭指令；部分服务仍在运行" : "机器人已关闭";
        else if (status.Hook && status.Bridge && status.OpenClaw)
            overallStatus.Text = "机器人运行中";
        else if (status.OpenClaw && !status.Hook)
            overallStatus.Text = "后台已运行，等待你登录 AI 微信";
        else if (status.AnyActive)
            overallStatus.Text = "机器人正在启动或部分服务未就绪";
        else
            overallStatus.Text = "机器人未运行";

        bool canStart = status.Disabled || !status.AnyActive;
        toggleButton.Text = canStart ? "启动整个程序" : "关闭整个程序";
        toggleButton.BackColor = canStart ? Color.FromArgb(30, 160, 105) : Color.FromArgb(195, 75, 65);
        toggleButton.Enabled = true;
        detailStatus.Text = status.Errors.Length == 0 ? "" : String.Join("；", status.Errors);
    }
}

internal static class BotSwitch
{
    [STAThread]
    private static void Main()
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        Application.Run(new BotSwitchForm());
    }
}
