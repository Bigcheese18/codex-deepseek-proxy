; Codex 模型快捷切换
; 文件: model_switch.ahk（AutoHotKey v2）
; 双击运行后，系统托盘会出现图标
; ─────────────────────────────────────────
; 快捷键：
;   Ctrl+Alt+1  → DeepSeek V4 Pro  (推理增强)
;   Ctrl+Alt+2  → DeepSeek V4 Flash(极速)
;   Ctrl+Alt+3  → DeepSeek V3      (通用)
;   Ctrl+Alt+4  → DeepSeek R1      (慢思考)
;   Ctrl+Alt+Q  → 打开迷你切换窗口
; ─────────────────────────────────────────

#Requires AutoHotkey v2.0
#SingleInstance Force

DASHBOARD := "http://127.0.0.1:38441"

; ── 切换函数 ─────────────────────────────
SwitchModel(modelId, modelName) {
    try {
        body := '{"model":"' . modelId . '"}'
        req := ComObject("Msxml2.XMLHTTP")
        req.open("POST", DASHBOARD . "/api/model/switch", false)
        req.setRequestHeader("Content-Type", "application/json")
        req.send(body)
        if (req.status = 200) {
            TrayTip("Codex 模型已切换", "✅ " . modelName . "`n重启 Codex 后生效", 2)
        } else {
            TrayTip("切换失败", "❌ HTTP " . req.status, 2)
        }
    } catch as e {
        TrayTip("切换失败", "❌ 无法连接控制台（请确认 dashboard.py 在运行）", 3)
    }
}

; ── 快捷键绑定 ───────────────────────────
^!1:: SwitchModel("deepseek-v4-pro",   "DeepSeek V4 Pro ⚡")
^!2:: SwitchModel("deepseek-v4-flash", "DeepSeek V4 Flash 🚀")
^!3:: SwitchModel("deepseek-v3",       "DeepSeek V3")
^!4:: SwitchModel("deepseek-r1",       "DeepSeek R1 🧠")

; ── Ctrl+Alt+Q 打开迷你切换窗口 ──────────
^!q:: {
    Run("chrome --app=" . DASHBOARD . "/quick --window-size=280,240 --window-position=1600,800")
    ; 如果不用 Chrome，改成：
    ; Run(DASHBOARD . "/quick")
}

; ── 托盘菜单 ─────────────────────────────
A_TrayMenu.Delete()
A_TrayMenu.Add("V4 Pro ⚡  (Ctrl+Alt+1)",  (*) => SwitchModel("deepseek-v4-pro",   "DeepSeek V4 Pro"))
A_TrayMenu.Add("V4 Flash 🚀 (Ctrl+Alt+2)", (*) => SwitchModel("deepseek-v4-flash", "DeepSeek V4 Flash"))
A_TrayMenu.Add("V3         (Ctrl+Alt+3)",  (*) => SwitchModel("deepseek-v3",       "DeepSeek V3"))
A_TrayMenu.Add("R1 🧠      (Ctrl+Alt+4)",  (*) => SwitchModel("deepseek-r1",       "DeepSeek R1"))
A_TrayMenu.Add()
A_TrayMenu.Add("打开迷你切换窗口 (Ctrl+Alt+Q)", (*) => Run(DASHBOARD . "/quick"))
A_TrayMenu.Add()
A_TrayMenu.Add("退出", (*) => ExitApp())
A_TrayMenu.Default := "打开迷你切换窗口 (Ctrl+Alt+Q)"

TraySetIcon("shell32.dll", 14)  ; 齿轮图标
A_IconTip := "Codex 模型切换器"
TrayTip("Codex 模型切换器已启动", "Ctrl+Alt+1~4 快捷切换`nCtrl+Alt+Q 打开窗口", 2)
