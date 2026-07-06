$ScriptDir = $PSScriptRoot
if (-not $ScriptDir) {
    $ScriptDir = Get-Location
}

# Start Python Local Background Service in a new PowerShell window
Write-Host "Launching Python Eye Monitoring Service..." -ForegroundColor Green
Start-Process powershell -WorkingDirectory $ScriptDir -ArgumentList "-NoExit", "-Command", "& '.\venv\Scripts\python.exe' 'backend\main.py'"

# Start React Front-end Dashboard in a new PowerShell window
Write-Host "Launching Vite React Dashboard Dev Server..." -ForegroundColor Green
Start-Process powershell -WorkingDirectory "$ScriptDir\frontend" -ArgumentList "-NoExit", "-Command", "npm run dev"

Write-Host "--------------------------------------------------------" -ForegroundColor Cyan
Write-Host "Eye Strain & Dry-Eye Diagnostics App is spinning up!" -ForegroundColor Cyan
Write-Host "1. The Python Service will run in the System Tray." -ForegroundColor White
Write-Host "2. The React Dashboard is accessible on http://localhost:5173" -ForegroundColor White
Write-Host "--------------------------------------------------------" -ForegroundColor Cyan
