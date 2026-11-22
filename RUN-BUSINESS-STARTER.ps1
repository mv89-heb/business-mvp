#requires -Version 5.1
<#
.SYNOPSIS
  One-command, developer-only setup and verification for eli-cohen-business-mvp.
.DESCRIPTION
  Checks prerequisites, safely clones/updates main, creates an isolated Python
  environment, installs dependencies, runs tests, and creates a dry-run plan.
  Optional menu actions start the localhost-only wizard, run read-only provider
  preflight, or start live provisioning after explicit cost and CREATE approvals.
  No cloud resources are created by the default local verification path.
#>
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$script:RepoUrl = "https://github.com/mv89-heb/eli-cohen-business-mvp.git"
$script:RepoName = "eli-cohen-business-mvp"
$script:RepoDir = $null
$script:PlanPath = $null
$script:PythonLauncher = $null
$script:LogPath = $null

function Write-Header([string]$Text) {
    Write-Host ""
    Write-Host ("=" * 68) -ForegroundColor DarkCyan
    Write-Host ("  " + $Text) -ForegroundColor Cyan
    Write-Host ("=" * 68) -ForegroundColor DarkCyan
}
function Write-Ok([string]$Text) { Write-Host "[בוצע] $Text" -ForegroundColor Green }
function Write-Warn([string]$Text) { Write-Host "[שים לב] $Text" -ForegroundColor Yellow }
function Write-Fail([string]$Text) { Write-Host "[שגיאה] $Text" -ForegroundColor Red }

function Refresh-ProcessPath {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user;$env:Path"
    $common = @(
        "$env:ProgramFiles\Git\cmd", "$env:ProgramFiles\Git\bin",
        "$env:LOCALAPPDATA\Programs\Git\cmd",
        "$env:LOCALAPPDATA\Programs\Python\Python311",
        "$env:LOCALAPPDATA\Programs\Python\Python311\Scripts",
        "$env:ProgramFiles\Python311", "$env:ProgramFiles\Python311\Scripts", "$env:WINDIR"
    )
    foreach ($path in $common) {
        if ($path -and (Test-Path $path) -and (($env:Path -split ';') -notcontains $path)) {
            $env:Path = "$path;$env:Path"
        }
    }
}

function Install-OptionalTool([string]$Id, [string]$DisplayName) {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) {
        Write-Warn "לא נמצא winget. יש להתקין את $DisplayName ידנית ואז להפעיל מחדש."
        return $false
    }
    $answer = Read-Host "לא נמצא $DisplayName. להתקין כעת דרך winget? (כן/לא)"
    if ($answer -notin @("כן", "y", "Y", "yes", "YES")) { return $false }
    Write-Host "מתקין $DisplayName..." -ForegroundColor Cyan
    & $winget.Source install --id $Id --exact --source winget --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "התקנת $DisplayName נכשלה (קוד $LASTEXITCODE)."
        return $false
    }
    Refresh-ProcessPath
    return $true
}

function Initialize-Prerequisites {
    Write-Header "בדיקת דרישות מקדימות"
    $git = Get-Command git -ErrorAction SilentlyContinue
    if (-not $git) {
        [void](Install-OptionalTool -Id "Git.Git" -DisplayName "Git")
        $git = Get-Command git -ErrorAction SilentlyContinue
    }
    if (-not $git) { throw "Git אינו זמין. התקן Git for Windows והפעל מחדש את הסקריפט." }

    $py = Get-Command py -ErrorAction SilentlyContinue
    if (-not $py) {
        [void](Install-OptionalTool -Id "Python.Python.3.11" -DisplayName "Python 3.11")
        $py = Get-Command py -ErrorAction SilentlyContinue
    }
    if (-not $py) { throw "Python Launcher (py) אינו זמין. התקן Python 3.11 עם Python Launcher." }

    & $py.Source -3.11 --version
    if ($LASTEXITCODE -ne 0) {
        [void](Install-OptionalTool -Id "Python.Python.3.11" -DisplayName "Python 3.11")
        Refresh-ProcessPath
        $py = Get-Command py -ErrorAction SilentlyContinue
        if (-not $py) { throw "לא נמצא Python Launcher לאחר ניסיון ההתקנה." }
        & $py.Source -3.11 --version
        if ($LASTEXITCODE -ne 0) { throw "Python 3.11 לא זמין. יש להתקין אותו לפני המשך." }
    }
    $script:PythonLauncher = $py.Source
    Write-Ok "Git ו-Python 3.11 זמינים."
}

function Resolve-Repository {
    Write-Header "איתור או הורדת המאגר"
    $candidate = $null
    if ($PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot ".git"))) {
        $candidate = (Resolve-Path $PSScriptRoot).Path
    }
    if (-not $candidate) { $candidate = Join-Path $HOME "BusinessAppStarter\$script:RepoName" }

    if (Test-Path $candidate) {
        if (-not (Test-Path (Join-Path $candidate ".git"))) {
            throw "התיקייה קיימת אך אינה מאגר Git: $candidate. לא יבוצע שינוי בתיקייה."
        }
        Push-Location $candidate
        try {
            $status = & git status --porcelain
            if ($LASTEXITCODE -ne 0) { throw "לא ניתן לבדוק את מצב המאגר." }
            if ($status) { throw "נמצאו שינויים מקומיים שלא נשמרו. שמור/גבה אותם לפני עדכון main: $candidate" }
            & git fetch origin main
            if ($LASTEXITCODE -ne 0) { throw "עדכון GitHub נכשל; המאגר המקומי לא עודכן." }
            & git switch main
            if ($LASTEXITCODE -ne 0) { throw "לא ניתן לעבור לענף main." }
            & git pull --ff-only origin main
            if ($LASTEXITCODE -ne 0) { throw "משיכת main נכשלה. לא בוצע איחוד כפוי." }
        }
        finally { Pop-Location }
    }
    else {
        $parent = Split-Path $candidate -Parent
        if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
        & git clone --branch main --single-branch $script:RepoUrl $candidate
        if ($LASTEXITCODE -ne 0) { throw "שכפול המאגר נכשל." }
    }
    $script:RepoDir = (Resolve-Path $candidate).Path
    Write-Ok "המאגר מוכן: $script:RepoDir"
}

function Invoke-CheckedCommand([string]$FilePath, [string[]]$Arguments, [string]$Description, [switch]$LogOutput) {
    Write-Host ""
    Write-Host $Description -ForegroundColor Cyan
    if ($LogOutput -and $script:LogPath) {
        & $FilePath @Arguments 2>&1 | Tee-Object -FilePath $script:LogPath -Append
    } else {
        & $FilePath @Arguments
    }
    $code = $LASTEXITCODE
    if ($code -ne 0) { throw "$Description נכשל (קוד $code). עוצרים כאן." }
}

function Ensure-PythonEnvironment {
    Push-Location $script:RepoDir
    try {
        $venvPython = Join-Path $script:RepoDir ".venv\Scripts\python.exe"
        if (-not (Test-Path $venvPython)) {
            Invoke-CheckedCommand -FilePath $script:PythonLauncher -Arguments @("-3.11", "-m", "venv", ".venv") -Description "יצירת סביבת Python מבודדת"
        }
        $venvPython = Join-Path $script:RepoDir ".venv\Scripts\python.exe"
        if (-not (Test-Path $venvPython)) { throw "סביבת Python לא נוצרה כראוי." }
        Invoke-CheckedCommand -FilePath $venvPython -Arguments @("-m", "pip", "install", "--upgrade", "pip") -Description "עדכון pip"
        Invoke-CheckedCommand -FilePath $venvPython -Arguments @("-m", "pip", "install", "-r", "requirements.txt") -Description "התקנת תלויות הפרויקט"
        $script:PythonLauncher = $venvPython
    }
    finally { Pop-Location }
}

function Invoke-LocalVerification {
    Initialize-Prerequisites
    Resolve-Repository
    $work = Join-Path $script:RepoDir "work"
    New-Item -ItemType Directory -Path $work -Force | Out-Null
    $script:LogPath = Join-Path $work ("setup-test-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".log")
    Ensure-PythonEnvironment
    Push-Location $script:RepoDir
    try {
        Invoke-CheckedCommand -FilePath $script:PythonLauncher -Arguments @("-m", "pytest", "-q") -Description "הרצת כל בדיקות הפרויקט" -LogOutput
        Write-Ok "כל בדיקות pytest עברו."
        $script:PlanPath = Join-Path $work ("test-business-plan-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".json")
        Invoke-CheckedCommand -FilePath $script:PythonLauncher -Arguments @("-m", "business_app.provisioning", "--output", $script:PlanPath) -Description "יצירת תוכנית Dry Run לעסק בדיקה"
        if (-not (Test-Path $script:PlanPath)) { throw "תוכנית ה-Dry Run לא נוצרה." }
        Write-Ok "תוכנית הבדיקה נשמרה: $script:PlanPath"
        Write-Ok "לא נוצרו משאבי ענן."
        Write-Host "יומן הבדיקות: $script:LogPath" -ForegroundColor Gray
    }
    finally { Pop-Location }
}

function Get-SecureValue([string]$Prompt, [int]$MinimumLength = 1) {
    $secure = Read-Host $Prompt -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
        if ([string]::IsNullOrWhiteSpace($plain) -or $plain.Length -lt $MinimumLength) {
            throw "הערך שהוזן קצר מדי או ריק."
        }
        return $plain
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
        $secure.Dispose()
    }
}

function Set-ProviderCredentials {
    Write-Header "הגדרת חיבורי ספקים — בחלון הנוכחי בלבד"
    Write-Warn "אין להדביק מפתחות API בצ'אט או לשמור אותם בקובץ. הקלט מוסתר."
    $owner = Read-Host "GitHub owner / username"
    $org = Read-Host "Neon organization ID המאושר"
    $workspace = Read-Host "Render workspace ID המאושר"
    if ([string]::IsNullOrWhiteSpace($owner) -or [string]::IsNullOrWhiteSpace($org) -or [string]::IsNullOrWhiteSpace($workspace)) {
        throw "כל מזהי הספקים נדרשים לבדיקת Preflight."
    }
    $env:GITHUB_OWNER = $owner.Trim()
    $env:NEON_ORG_ID = $org.Trim()
    $env:RENDER_WORKSPACE_ID = $workspace.Trim()
    $env:GITHUB_TOKEN = Get-SecureValue "GitHub API token" 10
    $env:NEON_API_KEY = Get-SecureValue "Neon API key" 10
    $env:RENDER_API_KEY = Get-SecureValue "Render API key" 10
    Write-Ok "הסודות הוגדרו בזיכרון התהליך בלבד. הם לא נשמרו בקבצים."
}

function Ensure-TestPlan {
    if ($script:PlanPath -and (Test-Path $script:PlanPath)) { return }
    if (-not $script:RepoDir) { throw "יש לבצע תחילה את שלב ההכנה המקומית." }
    $work = Join-Path $script:RepoDir "work"
    New-Item -ItemType Directory -Path $work -Force | Out-Null
    $script:PlanPath = Join-Path $work ("test-business-plan-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".json")
    Push-Location $script:RepoDir
    try {
        Invoke-CheckedCommand -FilePath $script:PythonLauncher -Arguments @("-m", "business_app.provisioning", "--output", $script:PlanPath) -Description "יצירת תוכנית Dry Run לעסק בדיקה"
    }
    finally { Pop-Location }
}

function Start-LocalWizard {
    Write-Header "הפעלת אשף המפתח המקומי"
    if (-not $script:RepoDir -or -not $script:PythonLauncher) { throw "יש לבצע תחילה הכנה מקומית ובדיקות." }
    if (-not $env:PROVISIONING_SESSION_SECRET) {
        $bytes = New-Object byte[] 48
        $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
        try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
        $env:PROVISIONING_SESSION_SECRET = [Convert]::ToBase64String($bytes)
    }
    if (-not $env:PROVISIONING_PASSWORD) {
        $env:PROVISIONING_PASSWORD = Get-SecureValue "בחר סיסמה לכניסה לאשף (לפחות 16 תווים)" 16
    }
    $listener = $false
    try {
        $listener = [bool](Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction Stop)
    }
    catch {
        # Fallback for Windows installations without the NetTCPIP cmdlet.
        try { $listener = [bool](Test-NetConnection -ComputerName 127.0.0.1 -Port 8765 -InformationLevel Quiet -WarningAction SilentlyContinue) } catch { $listener = $false }
    }
    if ($listener) {
        Write-Warn "נראה שכבר פועל שירות על localhost:8765. לא נפתח תהליך נוסף."
    } else {
        $proc = Start-Process -FilePath $script:PythonLauncher -ArgumentList @("-m", "business_app.provisioning_web") -WorkingDirectory $script:RepoDir -PassThru
        Start-Sleep -Seconds 2
        if ($proc.HasExited) { throw "שרת האשף נסגר מיד לאחר ההפעלה. בדוק את הודעת השגיאה." }
        Write-Ok "שרת האשף הופעל בתהליך מקומי (PID $($proc.Id))."
    }
    Start-Process "http://127.0.0.1:8765"
    Write-Warn "האשף מקומי בלבד. אין לחשוף אותו לרשת או ללקוחות."
}

function Invoke-ProviderPreflight {
    Write-Header "בדיקת ספקים לקריאה בלבד"
    if (-not $script:PythonLauncher) { throw "יש לבצע תחילה הכנה מקומית." }
    Set-ProviderCredentials
    Ensure-TestPlan
    Push-Location $script:RepoDir
    try {
        Invoke-CheckedCommand -FilePath $script:PythonLauncher -Arguments @("-m", "business_app.provisioning.execute", "--plan", $script:PlanPath, "--preflight-only") -Description "Preflight — קריאות GET בלבד"
    }
    finally { Pop-Location }
    Write-Ok "Preflight הסתיים. לא נוצרו או שונו משאבי ענן."
}

function Invoke-LiveProvisioning {
    Write-Header "הקמה חיה — יצירת משאבי ענן אמיתיים"
    Write-Warn "פעולה זו עשויה ליצור Repository פרטי, פרויקט Neon ושירות Render."
    Write-Warn "הסקריפט אינו יכול להבטיח זכאות Free או עלות אפס ב-Neon. חובה לבדוק זאת בחשבון Neon."
    $costChecked = Read-Host "האם בדקת בחשבון Neon שהפרויקט המסוים זכאי לעלות אפס? הקלד YES כדי להמשיך"
    if ($costChecked -cne "YES") { Write-Warn "ההקמה בוטלה. לא נשלחה בקשת יצירה."; return }
    if (-not $env:GITHUB_TOKEN -or -not $env:NEON_API_KEY -or -not $env:RENDER_API_KEY) { Set-ProviderCredentials }
    Ensure-TestPlan
    Push-Location $script:RepoDir
    try {
        # execute.py runs a fresh preflight before asking for CREATE and ZERO-COST.
        Invoke-CheckedCommand -FilePath $script:PythonLauncher -Arguments @("-m", "business_app.provisioning.execute", "--plan", $script:PlanPath) -Description "הקמה חיה — נדרשים אישורי CREATE ו-ZERO-COST"
    }
    finally { Pop-Location }
}

function Show-Menu {
    Write-Host ""
    Write-Host "Business App Starter — כלי מפתח" -ForegroundColor Cyan
    Write-Host "1. הכנה מלאה, בדיקות אוטומטיות ויצירת תוכנית בדיקה"
    Write-Host "2. הפעלת אשף ההקמה הגרפי (localhost בלבד)"
    Write-Host "3. הגדרת ספקים והרצת Preflight לקריאה בלבד"
    Write-Host "4. הקמה חיה של עסק בדיקה (רק לאחר אישור ובדיקת עלות)"
    Write-Host "5. יציאה"
    return Read-Host "בחר פעולה (1-5)"
}

try {
    Write-Header "מתחילים — Business App Starter"
    Write-Host "ברירת המחדל בטוחה: בדיקות מקומיות בלבד, ללא יצירת משאבי ענן."
    do {
        $choice = Show-Menu
        try {
            switch ($choice) {
                "1" { Invoke-LocalVerification }
                "2" { Start-LocalWizard }
                "3" { Invoke-ProviderPreflight }
                "4" { Invoke-LiveProvisioning }
                "5" { break }
                default { Write-Warn "בחירה לא מוכרת." }
            }
        }
        catch {
            Write-Fail $_.Exception.Message
            Write-Warn "התהליך נעצר. לא יתבצע ניסיון חוזר אוטומטי."
        }
    } while ($choice -ne "5")
}
catch {
    Write-Fail $_.Exception.Message
    exit 1
}
finally {
    foreach ($name in @("GITHUB_TOKEN", "NEON_API_KEY", "RENDER_API_KEY", "PROVISIONING_PASSWORD", "PROVISIONING_SESSION_SECRET")) {
        [Environment]::SetEnvironmentVariable($name, $null, "Process")
    }
}
