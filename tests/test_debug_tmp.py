def test_debug_tmp(tmp_path):
    from scripts.zip_release import BACKUP_NAME_RE
    print("TMPPARTS", tmp_path.parts)
    print("MATCHED", [p for p in tmp_path.parts if BACKUP_NAME_RE.search(p)])
