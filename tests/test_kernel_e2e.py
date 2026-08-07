"""Issue #25: the kernel end to end, through a real Jupyter kernelspec."""
import shutil

import pytest

pytest.importorskip("nbclient")
pytest.importorskip("jupyter_client")

import nbformat  # noqa: E402
from nbclient import NotebookClient  # noqa: E402

pytestmark = pytest.mark.java

MICROWAVE = """---- MODULE Microwave ----
EXTENDS Naturals
VARIABLES door, radiation
vars == <<door, radiation>>
Init == door = "closed" /\\ radiation = "off"
Open  == door' = "open"   /\\ UNCHANGED radiation
Close == door' = "closed" /\\ UNCHANGED radiation
Start == radiation' = "on" /\\ UNCHANGED door
Next == Open \\/ Close \\/ Start
Spec == Init /\\ [][Next]_vars
Safety == radiation = "on" => door = "closed"
===="""


@pytest.fixture(scope="module")
def kernel(tmp_path_factory):
    """Install the kernelspec into a throwaway Jupyter data dir."""
    import os

    from tlakit.jar import JarNotFound

    if shutil.which("java") is None:
        pytest.skip("java not on PATH")
    try:
        import tlakit

        tlakit.CliRunner()
    except JarNotFound as exc:
        pytest.skip(str(exc))

    data_dir = tmp_path_factory.mktemp("jupyter")
    os.environ["JUPYTER_DATA_DIR"] = str(data_dir)
    from tlakit.kernel.install import KERNEL_NAME, install

    install(user=True)
    return KERNEL_NAME


def run(cells, kernel):
    nb = nbformat.v4.new_notebook(
        cells=[nbformat.v4.new_code_cell(c) for c in cells]
    )
    NotebookClient(nb, timeout=180, kernel_name=kernel, allow_errors=True).execute()
    return nb


def outputs_of(cell):
    text = []
    for o in cell.get("outputs", []):
        if o.output_type == "stream":
            text.append(o.text)
        elif o.output_type == "error":
            text.append(o.evalue)
        else:
            data = o.get("data", {})
            text.append(data.get("text/html", "") or str(data.get("text/plain", "")))
    return "\n".join(text)


def test_a_bare_tla_cell_defines_and_parses_a_module(kernel):
    nb = run([MICROWAVE], kernel)
    assert "No error has been found" in outputs_of(nb.cells[0])


def test_a_bare_config_cell_checks_the_only_module(kernel):
    """No %tlc: prefix: with one module defined, the intent is unambiguous."""
    nb = run([MICROWAVE, "SPECIFICATION Spec\nINVARIANT Safety"], kernel)
    out = outputs_of(nb.cells[1])
    assert "Invariant violated" in out
    assert "radiation" in out


def test_python_still_runs_in_a_tla_notebook(kernel):
    """The reason to build on IPython rather than replace it."""
    nb = run(["import tlakit; print('v', tlakit.__version__)"], kernel)
    assert "v 0.1" in outputs_of(nb.cells[0])


def test_the_legacy_tlc_header_is_accepted(kernel):
    """kelvich/tlaplus_jupyter notebooks should keep working."""
    nb = run(
        [MICROWAVE, "%tlc:Microwave\nSPECIFICATION Spec\nINVARIANT Safety"], kernel
    )
    assert "Invariant violated" in outputs_of(nb.cells[1])


def test_a_config_with_no_module_explains_itself(kernel):
    nb = run(["SPECIFICATION Spec\nINVARIANT Safety"], kernel)
    assert "no module has been defined" in outputs_of(nb.cells[0])
