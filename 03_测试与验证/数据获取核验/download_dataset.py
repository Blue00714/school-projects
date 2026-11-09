"""Download and read-only verify the unchanged UCI workbook or public mirror."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import re
import shutil
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[2] / "20261014_电商数据与页面选型"
EVIDENCE = Path(__file__).resolve().parent
URL = "https://archive.ics.uci.edu/static/public/502/online%2Bretail%2Bii.zip"
SOURCE_PAGE = "https://archive.ics.uci.edu/dataset/502/online+retail+ii"
ARCHIVE = EVIDENCE / "online_retail_ii.zip"
MIRROR_URL = "https://raw.githubusercontent.com/KristerMartinez/online_retail_ii/main/online_retail_II.xlsx"


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download():
    if ARCHIVE.exists() and zipfile.is_zipfile(ARCHIVE):
        print("Reusing existing official archive", flush=True)
        return
    part = EVIDENCE / "online_retail_ii.zip.part"
    errors = []
    openers = [
        ("configured connection", urllib.request.build_opener()),
    ]
    registry_proxies = getattr(urllib.request, "getproxies_registry", lambda: {})()
    if registry_proxies:
        openers.append(("existing Windows system proxy", urllib.request.build_opener(urllib.request.ProxyHandler(registry_proxies))))
    openers.append(("direct connection", urllib.request.build_opener(urllib.request.ProxyHandler({}))))
    for label, opener in openers:
        print("Trying " + label, flush=True)
        try:
            request = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
            with opener.open(request, timeout=45) as response, part.open("wb") as stream:
                total = 0
                next_notice = 8 * 1024 * 1024
                while chunk := response.read(1024 * 1024):
                    stream.write(chunk)
                    total += len(chunk)
                    if total >= next_notice:
                        print(f"Downloaded {total / 1024 / 1024:.1f} MiB", flush=True)
                        next_notice += 8 * 1024 * 1024
            if not zipfile.is_zipfile(part):
                raise ValueError("The response is not a ZIP archive")
            part.replace(ARCHIVE)
            print(f"Official archive downloaded: {total} bytes", flush=True)
            return
        except Exception as exc:
            errors.append(label + ": " + type(exc).__name__)
            print("Connection failed: " + type(exc).__name__ + ": " + str(getattr(exc, "reason", exc)), flush=True)
    raise RuntimeError("Download failed; " + "; ".join(errors))


def mirror_download():
    original = ROOT / "online_retail_II.xlsx"
    if original.exists() and zipfile.is_zipfile(original):
        return original
    part = EVIDENCE / "online_retail_II.xlsx.part"
    proxies = getattr(urllib.request, "getproxies_registry", lambda: {})()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies))
    print("Downloading public workbook mirror; original UCI connection unavailable", flush=True)
    for attempt in range(1, 9):
        try:
            offset = part.stat().st_size if part.exists() else 0
            headers = {"User-Agent": "Mozilla/5.0", "Accept-Encoding": "identity"}
            if offset:
                headers["Range"] = f"bytes={offset}-"
            request = urllib.request.Request(MIRROR_URL, headers=headers)
            with opener.open(request, timeout=30) as response:
                if response.status == 206:
                    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
                    if not match or int(match[1]) != offset:
                        raise ValueError("Unexpected range response; refusing to append")
                    expected = int(match[3])
                    mode = "ab"
                elif response.status == 200:
                    offset = 0
                    expected = int(response.headers["Content-Length"])
                    mode = "wb"
                else:
                    raise ValueError(f"Unexpected HTTP status {response.status}")
                print(f"Attempt {attempt}: HTTP {response.status}, starting at {offset} / {expected} bytes", flush=True)
                with part.open(mode) as output:
                    total = offset
                    next_notice = (total // (4 * 1024 * 1024) + 1) * 4 * 1024 * 1024
                    while chunk := response.read(64 * 1024):
                        output.write(chunk)
                        total += len(chunk)
                        if total >= next_notice:
                            print(f"Downloaded {total / 1024 / 1024:.1f} MiB", flush=True)
                            next_notice += 4 * 1024 * 1024
            if total != expected:
                raise ValueError(f"Mirror download is incomplete: {total} / {expected}")
            if not zipfile.is_zipfile(part):
                raise ValueError("Mirror file is not an XLSX container")
            with zipfile.ZipFile(part) as contents:
                if "xl/workbook.xml" not in contents.namelist():
                    raise ValueError("Mirror file does not contain an Excel workbook")
                bad_member = contents.testzip()
                if bad_member:
                    raise ValueError(f"Workbook CRC check failed: {bad_member}")
            part.replace(original)
            return original
        except Exception as error:
            print(f"Mirror attempt {attempt} failed: {type(error).__name__}: {error}", flush=True)
            if attempt == 8 or isinstance(error, ValueError):
                raise


def main():
    using_mirror = "--mirror" in sys.argv
    if using_mirror:
        original = mirror_download()
    else:
        download()
        with zipfile.ZipFile(ARCHIVE) as archive:
            members = [item for item in archive.infolist() if item.filename.lower().endswith(".xlsx")]
            if len(members) != 1:
                raise ValueError("Expected one original workbook in the official archive")
            member = members[0]
            original = ROOT / Path(member.filename).name
            if not original.exists():
                with archive.open(member) as source, original.open("wb") as output:
                    shutil.copyfileobj(source, output)
            if original.stat().st_size != member.file_size:
                raise ValueError("Extracted workbook size mismatch")
    import openpyxl
    with zipfile.ZipFile(original) as contents:
        if contents.testzip() is not None:
            raise ValueError("Workbook ZIP integrity check failed")
    workbook = openpyxl.load_workbook(original, read_only=True, data_only=True)
    metadata = []
    for sheet in workbook:
        print(f"Read-only verification: {sheet.title}", flush=True)
        rows = sheet.iter_rows(values_only=True)
        headers = list(next(rows))
        count = 0
        first_date = last_date = None
        date_missing_or_invalid = 0
        date_index = headers.index("InvoiceDate")
        for row in rows:
            count += 1
            value = row[date_index]
            if isinstance(value, datetime):
                first_date = value if first_date is None else min(first_date, value)
                last_date = value if last_date is None else max(last_date, value)
            else:
                date_missing_or_invalid += 1
            if count % 200000 == 0:
                print(f"Read {count} data rows in {sheet.title}", flush=True)
        metadata.append({"sheet": sheet.title, "headers": headers, "reported_rows": sheet.max_row,
                         "actual_data_rows": count, "reported_columns": sheet.max_column,
                         "invoice_date_min": first_date.isoformat() if first_date else None,
                         "invoice_date_max": last_date.isoformat() if last_date else None,
                         "invoice_date_missing_or_invalid": date_missing_or_invalid})
    workbook.close()
    if using_mirror:
        if len(metadata) != 2 or sum(item["actual_data_rows"] for item in metadata) != 1067371:
            raise ValueError("Mirror worksheet dimensions do not match official dataset size")
        expected_headers = ["Invoice", "StockCode", "Description", "Quantity", "InvoiceDate", "Price", "Customer ID", "Country"]
        if any(item["headers"] != expected_headers for item in metadata):
            raise ValueError("Mirror headers differ from expected raw workbook schema")
    target = original
    source_hash = sha256(original)
    readme = ROOT / "数据说明.txt"
    if not readme.exists():
        readme.write_text(
            "Online Retail II 原始公开电商数据\n\n"
            f"来源：{SOURCE_PAGE}\n"
            + (f"实际下载：公开镜像 {MIRROR_URL}\n官方连接暂不可用，尚未取得官方原文件逐字节比对。\n" if using_mirror else f"实际下载：{URL}\n")
            +
            "提供方：UCI Machine Learning Repository\n"
            "依据官方说明，覆盖2009年12月1日至2011年12月9日。\n"
            "原始Excel未经清洗、删改或模拟补充，每行是交易明细，不等同于独立订单。\n"
            "单价单位为英镑；包含取消交易和缺失记录，使用前需核查。\n"
            "不包含原始库存、采购成本、点击或浏览数据。\n"
            "许可：CC BY 4.0，使用时注明数据来源。\n\n"
            + "工作表：\n" + "\n".join(
                item["sheet"] + "：" + ", ".join(str(h) for h in item["headers"])
                for item in metadata
            ) + "\n\nSHA256：" + source_hash + "\n",
            encoding="utf-8-sig",
        )
    result = {
        "downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_url": MIRROR_URL if using_mirror else URL,
        "source_page": SOURCE_PAGE,
        "source_kind": "public_mirror" if using_mirror else "official_archive",
        "mirror_source_page": "https://github.com/KristerMartinez/online_retail_ii/blob/main/online_retail_II.xlsx" if using_mirror else None,
        "official_byte_comparison_completed": False if using_mirror else None,
        "zip_crc_check_passed": True,
        "reader": "openpyxl " + openpyxl.__version__,
        "python_executable": sys.executable,
        "archive_bytes": ARCHIVE.stat().st_size if ARCHIVE.exists() else None,
        "workbook_bytes": target.stat().st_size,
        "workbook_sha256": source_hash,
        "local_workbook": str(target),
        "sheets": metadata,
        "scope": "Unchanged workbook delivery, ZIP CRC, actual row counts, headers and date bounds only; no cleaning, duplicate audit or model training",
    }
    (EVIDENCE / "download_record.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, default=str), flush=True)


if __name__ == "__main__":
    if "--probe" in sys.argv:
        proxies = getattr(urllib.request, "getproxies_registry", lambda: {})()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies))
        for url in [
            "https://archive.ics.uci.edu/ml/machine-learning-databases/00502/online_retail_II.xlsx",
            "https://raw.githubusercontent.com/KristerMartinez/online_retail_ii/main/online_retail_II.xlsx",
        ]:
            try:
                with opener.open(url, timeout=12) as response:
                    print(url, response.status, response.headers.get("Content-Length"), response.read(4), flush=True)
            except Exception as error:
                print(type(error).__name__, str(getattr(error, "reason", error)), flush=True)
    else:
        main()
