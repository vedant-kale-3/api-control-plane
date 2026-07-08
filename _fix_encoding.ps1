$content = Get-Content 'build.ps1' -Raw
$enc = New-Object System.Text.UTF8Encoding($true)
$path = (Resolve-Path 'build.ps1').Path
[System.IO.File]::WriteAllText($path, $content, $enc)
Write-Host "Written with UTF-8 BOM: $path"
