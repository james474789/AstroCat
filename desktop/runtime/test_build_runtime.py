"""Pure helpers of build_runtime.py. Run: python -m pytest desktop/runtime -q"""

import pytest

import build_runtime as b


def test_requirements_drop_only_conda_provided_packages():
    text = "fastapi==0.115.0\npyvips==2.2.3   # deep zoom\nPyVips>=2\nnumpy==1.26.4\n# comment\n"
    assert b.filtered_requirements(text) == "fastapi==0.115.0\nnumpy==1.26.4\n# comment\n"


def test_postgis_extension_files_keep_install_and_same_minor_upgrades(tmp_path):
    ext = tmp_path
    (ext / "postgis.control").write_text("comment = 'PostGIS'\ndefault_version = '3.6.2'\n")
    for name in ["postgis--3.6.2.sql", "postgis--ANY--3.6.2.sql", "postgis--3.6.0--ANY.sql",
                 "postgis--3.6.1--ANY.sql", "postgis--3.5.4--ANY.sql", "postgis--2.0.0--ANY.sql",
                 "postgis_raster--3.6.2.sql", "postgis--unpackaged--3.6.2.sql"]:
        (ext / name).write_text("")
    kept = sorted(p.name for p in b.postgis_extension_files(ext))
    assert kept == ["postgis--3.6.0--ANY.sql", "postgis--3.6.1--ANY.sql", "postgis--3.6.2.sql",
                    "postgis--ANY--3.6.2.sql", "postgis.control"]


def test_postgis_extension_files_fail_loudly_when_install_script_missing(tmp_path):
    (tmp_path / "postgis.control").write_text("default_version = '3.6.2'\n")
    with pytest.raises(SystemExit):
        b.postgis_extension_files(tmp_path)
