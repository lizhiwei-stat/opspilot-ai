# Real-browser login E2E from Windows (headless Microsoft Edge via Playwright), against the
# stack running in WSL2. Used because WSL's Chromium needs system libs (libasound2) = sudo.
# Requires uv on Windows. Passwords are read from the WSL .env and never printed.
param([string]$Repo = "/home/administrator/projects/opspilot-ai", [string]$Url = "http://localhost:5173",
      [string]$Shots = "", [string]$ProdUrl = "")
$uv = "$env:USERPROFILE\.local\bin\uv.exe"
# Copy files through the \\wsl.localhost share (byte-exact; piping `wsl cat` would re-encode UTF-8).
$share = "\\wsl.localhost\Ubuntu-24.04" + ($Repo -replace "/", "\")
$script = "$env:TEMP\login_e2e.py"
Copy-Item -Force "$share\frontend\e2e\login_e2e.py" $script
$envtxt = wsl -d Ubuntu-24.04 -- cat "$Repo/.env"
function Get-Val($k) { ($envtxt | Where-Object { $_ -match "^$k=" }) -replace "^$k=", "" }
$cases = @(@("admin@opspilot.local", "admin", "OPSPILOT_SEED_ADMIN_PASSWORD"),
           @("alice@opspilot.local", "user", "OPSPILOT_SEED_USER_PASSWORD"))
foreach ($c in $cases) {
  $env:OPSPILOT_E2E_PASSWORD = Get-Val $c[2]
  & $uv run -q --no-project --python 3.11 --with playwright==1.64.1 python $script `
    --url $Url --email $c[0] --expect-role $c[1] --channel msedge
}
# Stage 2: knowledge base / chunk viewer / 4-mode search / QA / evals pages (as alice).
$rag = "$env:TEMP\rag_e2e.py"
Copy-Item -Force "$share\frontend\e2e\rag_e2e.py" $rag
$env:OPSPILOT_E2E_PASSWORD = Get-Val "OPSPILOT_SEED_USER_PASSWORD"
$extra = @(); if ($Shots) { $extra = @("--screenshots", $Shots) }
& $uv run -q --no-project --python 3.11 --with playwright==1.64.1 python $rag `
  --url $Url --email alice@opspilot.local --channel msedge @extra
Remove-Item Env:OPSPILOT_E2E_PASSWORD
# Stage 3: Agent workbench (alice + admin approval).
$ag = "$env:TEMP\agent_e2e.py"
Copy-Item -Force "$share\frontend\e2e\agent_e2e.py" $ag
$env:OPSPILOT_E2E_USER_PASSWORD = Get-Val "OPSPILOT_SEED_USER_PASSWORD"
$env:OPSPILOT_E2E_ADMIN_PASSWORD = Get-Val "OPSPILOT_SEED_ADMIN_PASSWORD"
& $uv run -q --no-project --python 3.11 --with playwright==1.64.1 python $ag --url $Url --channel msedge @extra
Remove-Item Env:OPSPILOT_E2E_USER_PASSWORD, Env:OPSPILOT_E2E_ADMIN_PASSWORD
# Stage 4: MCP incident -> approval (return-for-revision, approve) -> ticket, audit.
$m4 = "$env:TEMP\mcp_e2e.py"
Copy-Item -Force "$share\frontend\e2e\mcp_e2e.py" $m4
$env:OPSPILOT_E2E_USER_PASSWORD = Get-Val "OPSPILOT_SEED_USER_PASSWORD"
$env:OPSPILOT_E2E_ADMIN_PASSWORD = Get-Val "OPSPILOT_SEED_ADMIN_PASSWORD"
Push-Location $env:TEMP  # mcp_e2e imports helpers from agent_e2e.py (copied above)
& $uv run -q --no-project --python 3.11 --with playwright==1.64.1 python $m4 --url $Url --channel msedge @extra
Pop-Location
Remove-Item Env:OPSPILOT_E2E_USER_PASSWORD, Env:OPSPILOT_E2E_ADMIN_PASSWORD
# Stage 5: enterprise web platform full flow (KB upload -> user agent run with SSE reconnect ->
# ticket draft -> admin approval -> support transition -> dashboard), screenshots.
# -ProdUrl additionally runs the same flow against the nginx production build (127.0.0.1:5180).
$s5 = "$env:TEMP\stage5_e2e.py"
Copy-Item -Force "$share\frontend\e2e\stage5_e2e.py" $s5
$env:OPSPILOT_E2E_USER_PASSWORD = Get-Val "OPSPILOT_SEED_USER_PASSWORD"
$env:OPSPILOT_E2E_ADMIN_PASSWORD = Get-Val "OPSPILOT_SEED_ADMIN_PASSWORD"
& $uv run -q --no-project --python 3.11 --with playwright==1.64.1 python $s5 --url $Url --channel msedge @extra
if ($ProdUrl) {
  $p = @(); if ($Shots) { $p = @("--screenshots", (Join-Path $Shots "prod")) }
  & $uv run -q --no-project --python 3.11 --with playwright==1.64.1 python $s5 --url $ProdUrl --channel msedge @p
}
Remove-Item Env:OPSPILOT_E2E_USER_PASSWORD, Env:OPSPILOT_E2E_ADMIN_PASSWORD
