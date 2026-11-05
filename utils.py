import os
import shutil
import requests
import subprocess
import sys
import glob

_scraper = None

# スクレイパーを取得
def get_scraper():
    global _scraper
    if _scraper is None:
        from curl_cffi import requests as cffi_requests
        _scraper = cffi_requests.Session(impersonate="chrome")
        _scraper.headers.update({
            "Accept-Language": "en-US,en;q=0.9",
        })
    return _scraper

# 致命的なエラーメッセージを表示し、スクリプトを強制終了する
def panic(message: str):
    print(message, file=sys.stderr)
    exit(1)

# ダウンロード処理
def download(link, out, headers=None, use_scraper=False):
    dir_name = os.path.dirname(out)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)

    if os.path.exists(out):
        print(f"{out} already exists skipping download")
        return

    if use_scraper:
        print(f"  -> [DEBUG] Downloading with scraper: {link}")

    if headers is None:
        headers = {}
    if "Referer" not in headers:
        headers["Referer"] = "https://www.apkmirror.com/"

    session = get_scraper() if use_scraper else requests

    r = None
    try:
        r = session.get(link, stream=True, headers=headers)
        r.raise_for_status()
        with open(out, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
    except Exception as e:
        status = getattr(r, "status_code", "Unknown") if r is not None else "Unknown"
        print(f"\n  -> [FATAL ERROR] Download blocked (Status: {status})")
        print(f"  -> Target: {link}")
        panic(f"Error details: {e}")
    finally:
        if r is not None:
            try:
                r.close()
            except Exception:
                pass

# 外部シェルコマンドを実行し、失敗時はログを出力して異常終了する
def run_command(command: list[str]):
    cmd = subprocess.run(command, capture_output=True, shell=True)
    try:
        cmd.check_returncode()
    except subprocess.CalledProcessError:
        print(cmd.stdout)
        print(cmd.stderr)
        exit(1)

# APKEditorを使用して分割されたAPKをマージする
def merge_apk(path: str):
    print(f"  -> Merging APK: {path}")
    subprocess.run(
        ["java", "-jar", "./bins/apkeditor.jar", "m", "-extractNativeLibs", "true", "-i", path]
    ).check_returncode()

# Morphe CLIでパッチを適用・署名する
def patch_apk(
    cli: str,
    patches: str,
    apk: str,
    includes: list[str] | None = None,
    excludes: list[str] | None = None,
    out: str | None = None,
):
    includes = includes or []
    excludes = excludes or []

    command = [
        "java", "-jar", cli, "patch",
        "-p", patches,
        "--continue-on-error",
        "--keystore", "ks.keystore",
        "--keystore-entry-password", "123456789",
        "--keystore-password", "123456789",
        "--signer", "jhc",
        "--keystore-entry-alias", "jhc",
    ]

    # ファイル検索ロジックに依存しないよう、outが指定されている場合は-oオプションで出力先を強制する
    if out is not None:
        command += ["-o", out]

    for i in includes:
        command += ["-e", i]
    for e in excludes:
        command += ["-d", e]

    command.append(apk)

    print(f"Executing: {' '.join(command)}")
    result = subprocess.run(command, capture_output=True, text=True)

    if result.stdout:
        print(result.stdout)

    if result.returncode != 0:
        print("--- CLI Error Output ---", file=sys.stderr)
        print(result.stdout, file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        print("------------------------", file=sys.stderr)
        result.check_returncode()

    # 指定したパスにファイルが正常に出力されたかだけを確認する
    if out is not None:
        if not os.path.exists(out):
            panic(f"Expected CLI output not found at: {out}")
        print(f"  -> [DEBUG] Detected generated APK: {out}")

# リリース用処理
def publish_release(tag: str, files: list[str], message: str, title=""):
    key = os.environ.get("GITHUB_TOKEN")
    if key is None:
        raise Exception("GITHUB_TOKEN is not set")
    if len(files) == 0:
        raise Exception("Files should have at least one item")

    def release_exists(t: str) -> bool:
        result = subprocess.run(
            ["gh", "release", "view", t],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        return result.returncode == 0

    if release_exists(tag):
        print(f"Release '{tag}' already exists — deleting...")
        subprocess.run(["gh", "release", "delete", tag, "-y"],
                       env=os.environ.copy()).check_returncode()

        print(f"Deleting tag '{tag}' via API...")
        api_cmd = [
            "gh", "api", "--method", "DELETE",
            f"/repos/{os.environ['GITHUB_REPOSITORY']}/git/refs/tags/{tag}"
        ]
        subprocess.run(api_cmd, env=os.environ.copy()).check_returncode()
        print("Old release removed. Creating new one...")

    command = ["gh", "release", "create", "--latest", tag, "--notes", message, "--title", title]
    command.extend(files)
    subprocess.run(command, env=os.environ.copy()).check_returncode()