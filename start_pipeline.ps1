# load .env variables
if (Test-Path ".env") {
    Get-Content .env | ForEach-Object {
        if ($_ -match "^\s*([^#][^=]+)=(.*)$") {
            $name = $matches[1]
            $value = $matches[2]
            Set-Item -Path Env:$name -Value $value
        }
    }
}

Write-Host "Environment loaded"

# activate venv
if (Test-Path ".\venv\Scripts\Activate.ps1") {
    . .\venv\Scripts\Activate.ps1
}

Write-Host "Running pipeline..."

python main.py
