Set-Location 'C:\CV- Toposheet\_build'

Remove-Item 'CVToposheet_1.0.1.0_x64.msix' -Force -ErrorAction SilentlyContinue
Remove-Item 'msix_staging' -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path 'msix_staging' | Out-Null

Copy-Item 'AppxManifest.xml' 'msix_staging\'
Copy-Item 'Assets' 'msix_staging\Assets' -Recurse
Copy-Item 'dist\CVToposheet\*' 'msix_staging\' -Recurse -Force

Write-Host 'Staging ready. Running MakeAppx...'

$makeappx = 'C:\Program Files (x86)\Windows Kits\10\bin\10.0.26100.0\x64\makeappx.exe'
& $makeappx pack /d 'msix_staging' /p 'CVToposheet_1.0.1.0_x64.msix' /nv /o

if (Test-Path 'CVToposheet_1.0.1.0_x64.msix') {
    $size = [math]::Round((Get-Item 'CVToposheet_1.0.1.0_x64.msix').Length / 1MB, 1)
    Write-Host "Done. Size: $size MB"
} else {
    Write-Host "ERROR: MSIX not created"
}
