import sys
import os
import re
import time
import subprocess
import argparse
import glob
import shutil
from functools import cmp_to_key

# utils.py をインポート
from utils import patch_apk, merge_apk, panic
from download_bins import download_apkeditor, download_morphe_cli

# apkmirror.py（または scraper.py）からインポート
from apkmirror import Version, Variant, get_variants, download_apk

# 外部ライブラリによるプロセス強制終了を例外に変換して安全に捕捉可能にする
class ProcessExitException(BaseException): pass
def prevent_exit(code=0):
    raise ProcessExitException(f"Process exit prevented! (exit code {code})")
    
sys.exit = prevent_exit
os._exit = prevent_exit


# プレリリースを含むバージョン文字列を数値的に比較し、v1がv2より新しければTrueを返す
def version_greater(v1: str | None, v2: str | None) -> bool:
    if not v1: return False
    if not v2: return True
    def normalize(v: str):
        v = v.lstrip('v')
        parts = v.split('-', 1)
        main_part = parts[0]
        prerelease_part = parts[1] if len(parts) > 1 else ""
        main_nums = [int(n) for n in re.findall(r'\d+', main_part)[:3]]
        while len(main_nums) < 3: main_nums.append(0)
        pre_parts = [int(p) if p.isdigit() else p for p in re.split(r'(\d+)', prerelease_part) if p]
        return main_nums, pre_parts

    nums1, pre1 = normalize(v1)
    nums2, pre2 = normalize(v2)

    for i in range(3):
        if nums1[i] != nums2[i]: return nums1[i] > nums2[i]

    if not pre1 and pre2: return True
    if pre1 and not pre2: return False
    for p1, p2 in zip(pre1, pre2):
        if p1 != p2:
            return p1 > p2 if type(p1) == type(p2) else str(p1) > str(p2)
    return len(pre1) > len(pre2)


# リポジトリのリリース一覧を取得し、バージョン文字列でソートして最新のStableとPre-releaseを返す
def get_latest_releases(repo: str, require_mpp: bool = False) -> dict:
    import json
    print(f"  -> Fetching release history for {repo}...")
    cmd = ["gh", "api", f"repos/{repo}/releases?per_page=30"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        releases = json.loads(result.stdout)
    except Exception as e:
        print(f"  -> [WARNING] Failed to fetch releases for {repo}: {e}")
        return {"stable": None, "pre": None}
        
    valid_stable = []
    valid_pre = []

    for r in releases:
        tag = r.get("tag_name")
        is_pre = r.get("prerelease", False)
        
        if require_mpp:
            has_mpp = any(a.get("name", "").endswith(".mpp") for a in r.get("assets", []))
            if not has_mpp: continue
        
        if is_pre:
            valid_pre.append(tag)
        else:
            valid_stable.append(tag)

    def cmp_versions(v1, v2):
        if v1 == v2: return 0
        return 1 if version_greater(v1, v2) else -1

    if valid_stable:
        valid_stable.sort(key=cmp_to_key(cmp_versions), reverse=True)
    if valid_pre:
        valid_pre.sort(key=cmp_to_key(cmp_versions), reverse=True)

    return {
        "stable": valid_stable[0] if valid_stable else None,
        "pre": valid_pre[0] if valid_pre else None
    }


# GitHubリリースを作成または既存のリリースにアセットを追記する
def publish_github_release(tag_name: str, files: list, message: str, title: str, is_prerelease: bool):
    print(f"  -> Attempting to publish/upload to {tag_name}...")
    check_cmd = ["gh", "release", "view", tag_name]
    res = subprocess.run(check_cmd, capture_output=True)
    
    if res.returncode == 0:
        print("  -> Release already exists! Uploading assets to the existing release...")
        subprocess.run(["gh", "release", "upload", tag_name] + files + ["--clobber"], check=True)
    else:
        print("  -> Creating new release...")
        cmd_create = ["gh", "release", "create", tag_name] + files + ["-t", title, "-n", message]
        if is_prerelease: cmd_create.append("--prerelease")
        try:
            subprocess.run(cmd_create, check=True)
        except subprocess.CalledProcessError:
            print("  -> Create failed (likely race condition). Falling back to upload...")
            subprocess.run(["gh", "release", "upload", tag_name] + files + ["--clobber"], check=True)


# CLIのテキスト出力を解析し、以前のJSONと全く同じ構造のメタデータを生成する
def extract_patches_metadata(cli_path: str, mpp_path: str) -> list:
    print(f"  -> Extracting patch list dynamically from {mpp_path} via CLI (Text Parsing Mode)...")
    cmd = ["java", "-jar", cli_path, "list-patches", f"--patches={mpp_path}", "-p", "-v"]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, encoding='utf-8', errors='ignore')
        out = result.stdout
    except subprocess.CalledProcessError as e:
        panic(f"Failed to extract patches from CLI. Error: {e.stderr}")
    except Exception as e:
        panic(f"Failed to execute CLI command. Error: {e}")

    patches = []
    current_patch = None
    current_package = None
    in_versions = False

    for line in out.splitlines():
        s_line = line.strip()
        if not s_line: continue
        if s_line.startswith('Index:'):
            current_patch = {"name": "", "compatiblePackages": []}
            patches.append(current_patch)
            current_package = None
            in_versions = False
        elif s_line.startswith('Name:') and current_patch is not None:
            current_patch["name"] = s_line[5:].strip()
        elif s_line.startswith('Package name:'):
            pkg_name = s_line.split('Package name:', 1)[1].strip()
            current_package = {"name": pkg_name, "versions": []}
            if current_patch is not None:
                current_patch["compatiblePackages"].append(current_package)
            in_versions = False
        elif s_line.startswith('Compatible versions:'):
            in_versions = True
        elif in_versions and current_package is not None:
            if s_line[0].isdigit():
                current_package["versions"].append(s_line)
            else:
                in_versions = False

    if not patches: panic("Could not parse any patch data from CLI text output.")
    return patches


# 対象アプリがサポートするAPKバージョンのリストを抽出し、直近5件を古い順にソートして返す
def get_supported_versions(patches_list: list, package_name: str) -> list:
    versions_set = set()
    for patch in patches_list:
        compat = patch.get("compatiblePackages")
        if isinstance(compat, dict) and package_name in compat:
            if compat[package_name]: versions_set.update(compat[package_name])
        elif isinstance(compat, list):
            for pkg in compat:
                if isinstance(pkg, dict) and pkg.get("name") == package_name:
                    if pkg.get("versions"): versions_set.update(pkg.get("versions"))

    def parse_ver(v):
        return [int(x) for x in re.findall(r'\d+', v)]
    
    sorted_versions = sorted(list(versions_set), key=parse_ver)
    return sorted_versions[-5:]


# 指定されたAPKバージョンと互換性のあるすべてのパッチを抽出する
def get_patches_for_version(patches_list: list, package_name: str, target_version: str) -> list:
    patches = []
    for patch in patches_list:
        patch_name = patch.get("name")
        compat = patch.get("compatiblePackages")
        supports_version = False

        if not compat: 
            supports_version = True
        elif isinstance(compat, dict) and package_name in compat:
            versions = compat[package_name]
            if not versions or target_version in versions: supports_version = True
        elif isinstance(compat, list):
            for pkg in compat:
                if isinstance(pkg, dict) and pkg.get("name") == package_name:
                    versions = pkg.get("versions", [])
                    if not versions or target_version in versions: supports_version = True
                    break

        if supports_version:
            patches.append(patch_name)
    return patches


# ビルド環境の不要な一時ファイルや過去のAPKを削除してクリーンアップする
def clean_workspace():
    for f in ["line_base.apk", "line_base.apkm", "line_base_merged.apk", "bins/patches.mpp"]:
        if os.path.exists(f): os.remove(f)
    for f in os.listdir("."):
        # 古いMorphe APKを削除するように変更
        if f.endswith(".apk") and "Morphe" in f: os.remove(f)


# APKMirrorから対象バージョンのLINEをダウンロードし、必要ならマージする
def fetch_and_prepare_line_apk(target_version: str) -> tuple[str, str]:
    print(f"\n  -> [DOWNLOAD ROUTINE] Fetching LINE v{target_version} from APKMirror...")
    
    dashed_version = target_version.replace('.', '-')
    release_url = f"https://www.apkmirror.com/apk/line-corporation/line/line-calls-messages-{dashed_version}-release/"
    
    v_obj = Version(version=target_version, link=release_url)
    
    print(f"  -> Fetching variants from: {release_url}")
    variants = get_variants(v_obj)
    
    if not variants:
        raise Exception(f"No variants found for LINE v{target_version}")

    # アーキテクチャの絞り込み (arm64-v8a または universal を優先)
    selected_variant = next(
        (v for v in variants if "arm64-v8a" in v.architecture.lower() or "universal" in v.architecture.lower()),
        variants[0]
    )

    print(f"  -> Selected variant: Architecture={selected_variant.architecture}, Bundle={selected_variant.is_bundle}")
    
    ext = ".apkm" if selected_variant.is_bundle else ".apk"
    download_path = f"line_base{ext}"
    
    if os.path.exists(download_path):
        os.remove(download_path)

    download_apk(selected_variant, download_path)
    
    if not os.path.exists(download_path):
        raise Exception("Download completed but file is missing.")
        
    print(f"  -> [SUCCESS] Downloaded: {download_path}")

    # Bundleならマージ処理へ
    if selected_variant.is_bundle:
        merged_path = "line_base_merged.apk"
        merge_apk(download_path)
        
        if not os.path.exists(merged_path):
            panic(f"Merge failed: {merged_path} not found.")
            
        return merged_path, target_version
    else:
        return download_path, target_version


# パッチの取得からAPKのダウンロード、パッチ適用、GitHubリリース作成までの一連のパイプライン処理
def process(tag: str, is_pre: bool, target_app: str, upstream_repo: str):
    print(f"\n=======================================================")
    print(f"INITIATING BUILD PIPELINE FOR: {tag} ({target_app.upper()})")
    print(f"=======================================================")
    
    clean_workspace()

    print("\n[STEP 3] Downloading patches & CLI...")
    subprocess.run(["gh", "release", "download", tag, "-R", upstream_repo, "-p", "*.mpp", "-O", "bins/patches.mpp"], check=True)
    download_apkeditor()
    download_morphe_cli()

    patches_list = extract_patches_metadata("bins/morphe-cli.jar", "bins/patches.mpp")
    if not patches_list: panic("Extracted patch list is empty!")
    print(f"  -> Successfully extracted {len(patches_list)} patches metadata.")

    outputs = []
    included_apps_text = []

    if target_app in ["line", "all"]:
        print("\n[LINE] Fetching target versions...")
        line_versions = get_supported_versions(patches_list, "jp.naver.line.android")
        print(f"  -> Supported versions by patcher: {line_versions}")
        
        final_line_ver = None
        line_input = None
        for version in reversed(line_versions):
            try:
                line_input, final_line_ver = fetch_and_prepare_line_apk(version)
                break 
            except Exception as e:
                print(f"  -> [FAILED] Could not download v{version}: {e}")
                print("  -> Retrying with an older version...")
                time.sleep(2)
        
        if line_input and final_line_ver:
            try:
                print(f"\n[STEP 4] Applying Morphe Patches to v{final_line_ver}...")
                line_patches = get_patches_for_version(patches_list, "jp.naver.line.android", final_line_ver)
                
                # 出力APK名
                output_apk = f"LINE-Morphe-v{final_line_ver}.apk"
                patch_apk("bins/morphe-cli.jar", "bins/patches.mpp", line_input, includes=line_patches, excludes=[], out=output_apk)
                
                if os.path.exists(output_apk):
                    outputs.append(output_apk)
                    included_apps_text.append(f"LINE v{final_line_ver}")
                    print(f"  -> [SUCCESS] {output_apk} successfully built!")
                else:
                    panic(f"Failed to build {output_apk}")
                    
            except BaseException as e: 
                print(f"  -> [WARNING] LINE build failed: {e}")
        else:
            print("  -> [FATAL] All fallback attempts failed for LINE.")

    if not outputs:
        panic("No APKs were built. Aborting release.")

    print(f"\n[STEP 5] Publishing release to GitHub...")
    apps_str = "\n".join(included_apps_text)
    message = f"Changelogs:\n[Andrew's Patches {tag}](https://github.com/{upstream_repo}/releases/tag/{tag})\n\n### Included Apps:\n{apps_str}"
    
    publish_github_release(tag, outputs, message, f"{tag}", is_pre)
    print("  -> [DONE] Release successfully published!")


def main():
    parser = argparse.ArgumentParser(description="Morphe Auto Builder")
    parser.add_argument("--app", choices=["line", "all"], default="all", help="Which app to build")
    args = parser.parse_args()

    repo_url = "monsivamon/line-apk" 
    upstream_repo = "andrewliang25/morphe-patches"

    print(f"\n[STEP 1] Fetching release history... (Mode: {args.app.upper()})")
    upstream = get_latest_releases(upstream_repo, require_mpp=True)
    my_repo = get_latest_releases(repo_url, require_mpp=False)
    
    print(f"\n[VERSION INFO]")
    print(f"  -> Upstream ({upstream_repo}): Stable: {upstream['stable'] or 'None'} | Pre-release: {upstream['pre'] or 'None'}")
    print(f"  -> My Repo ({repo_url}): Stable: {my_repo['stable'] or 'None'} | Pre-release: {my_repo['pre'] or 'None'}")
    
    print("\n[STEP 2] Verifying build history for updates...")
    build_targets = []
    
    if upstream["stable"] and version_greater(upstream["stable"], my_repo["stable"]):
        print(f"  -> [NEW UPDATE] Stable: {my_repo['stable']} -> {upstream['stable']}")
        build_targets.append({"tag": upstream["stable"], "is_pre": False})
        
    if upstream["pre"] and version_greater(upstream["pre"], my_repo["pre"]):
        print(f"  -> [NEW UPDATE] Pre-release: {my_repo['pre']} -> {upstream['pre']}")
        build_targets.append({"tag": upstream["pre"], "is_pre": True})

    if not build_targets:
        print("  -> [EXIT] No new updates found. Skipping build.")
        return

    for target in build_targets:
        try:
            process(target["tag"], target["is_pre"], args.app, upstream_repo)
        except ProcessExitException:
            pass

if __name__ == "__main__":
    main()