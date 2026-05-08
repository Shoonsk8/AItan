"""Update DB must scan ONLY the project's own configured directories.

Past regression (d7257f9): execute_generate appended every global
watch_dir to the scan list, silently pulling unrelated files into
whichever project was being updated. The user reported this multiple
times. This test pins the scope so it can't come back.

We don't need to run the full GUI — we read the source of
execute_generate and assert it doesn't reference watch_dirs in the
dir-collection block. Cheap text-level check; the real GUI test
would need a fake project + filesystem and is overkill for the
regression we're guarding.
"""
import inspect
import re

import aisearch_settings_db


def test_execute_generate_does_not_scan_watch_dirs():
    src = inspect.getsource(aisearch_settings_db._DbMixin.execute_generate)
    # Find the block that builds dirs_flags / dirs / no_subs — should
    # NOT walk watch_dirs and append them to dirs_flags.
    # Bug pattern that was here before:
    #     for _wd in cfg.load_config().get("watch_dirs", []):
    #         if os.path.isdir(_wd) and ...:
    #             dirs_flags.append((_wd, True))
    bad = re.search(r'dirs_flags\.append\([^)]*watch', src)
    assert bad is None, (
        "execute_generate appends watch_dirs to dirs_flags — that's "
        "the regression d7257f9 introduced and the user has rejected. "
        "Update DB must scan ONLY the project's configured dirs.")
    # Belt-and-suspenders: no executable reference to watch_dirs in this
    # function — comments may explain the rule. Strip comments first.
    code_only = "\n".join(
        line.split("#", 1)[0] for line in src.splitlines()
    )
    suspicious = re.search(r'watch_dirs', code_only)
    assert suspicious is None, (
        "execute_generate has live (non-comment) code referencing "
        "watch_dirs — that's the vector for cross-project file "
        "pollution. Move the reference out of this function or update "
        "the test if there's a legitimate new need.")
