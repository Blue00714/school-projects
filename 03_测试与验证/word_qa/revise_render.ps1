$ErrorActionPreference = 'Stop'
$reportPath = Join-Path $PSScriptRoot '..\..\01_交付内容\基于FastAPI的智能电商运营管理系统_前三章参考稿.docx'
$reportPath = [System.IO.Path]::GetFullPath($reportPath)
$reportPdf = Join-Path $PSScriptRoot 'revision_final.pdf'
$reportApp = New-Object -ComObject Word.Application
$reportApp.Visible = $false
$reportApp.DisplayAlerts = 0
try {
    $reportDoc = $reportApp.Documents.Open($reportPath, $false, $false)
    $null = $reportDoc.Fields.Update()
    foreach ($reportToc in $reportDoc.TablesOfContents) { $null = $reportToc.Update() }
    $reportDoc.Repaginate()
    foreach ($reportToc in $reportDoc.TablesOfContents) { $null = $reportToc.Update() }
    $reportDoc.Repaginate()
    $reportDoc.Save()
    $reportDoc.ExportAsFixedFormat($reportPdf, 17)
    $reportDoc.Close(0)
    'Native field refresh, save and PDF export succeeded'
} finally {
    try { $reportApp.Quit() } catch {
        if ($_.Exception.HResult -ne -2147023170) { throw }
        'Native editor had already disconnected after document close'
    }
}
