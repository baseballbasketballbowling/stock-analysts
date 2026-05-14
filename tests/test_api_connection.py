"""API接続診断スクリプト。ワークフローのデバッグ用。"""
import sys
import traceback

def main():
    # 1. インポート確認
    try:
        import jquantsapi
        print(f"[OK] jquantsapi {jquantsapi.__version__} をインポート")
    except Exception as e:
        print(f"[FAIL] import jquantsapi: {e}")
        sys.exit(1)

    # 2. クライアント初期化
    try:
        cli = jquantsapi.ClientV2()
        print("[OK] ClientV2() 初期化成功")
    except Exception as e:
        print(f"[FAIL] ClientV2(): {e}")
        traceback.print_exc()
        sys.exit(1)

    # 3. 銘柄一覧取得
    try:
        df = cli.get_eq_master()
        print(f"[OK] get_eq_master(): {len(df)} 件, カラム: {list(df.columns[:5])}")
    except Exception as e:
        print(f"[FAIL] get_eq_master(): {e}")
        traceback.print_exc()
        sys.exit(1)

    # 4. 財務サマリ取得（1日分）
    try:
        df2 = cli.get_fin_summary(date_yyyymmdd="20240101")
        print(f"[OK] get_fin_summary(): {len(df2)} 件")
    except Exception as e:
        print(f"[FAIL] get_fin_summary(): {e}")
        traceback.print_exc()
        sys.exit(1)

    print("[DONE] 全テスト通過")

if __name__ == "__main__":
    main()
