"""生成スクリプト（pptxgenjs）で作った pptx を検査する補助（#2065）。

汎用テンプレートのテストと発表資料のテストが同じ読み方で pptx を読むため、ここに 1 つだけ置く。
pptx は zip と XML なので、標準ライブラリだけで読む。
"""

from __future__ import annotations

import fcntl
import hashlib
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

EMU = 914400  # 1 インチ
NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}
TEMPLATE = Path(__file__).resolve().parents[1] / "assets" / "slides"


def ensure_node_modules(slides_dir: Path) -> None:
    """`slides/` に `npm ci` で依存を入れる。並列の実行どうしはロックで順に回す。"""
    key = hashlib.sha256(str(slides_dir.resolve()).encode()).hexdigest()[:16]
    lock = Path(tempfile.gettempdir()) / f"ndf-npm-ci-{key}.lock"
    with open(lock, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            if not (slides_dir / "node_modules" / "pptxgenjs").is_dir():
                subprocess.run(
                    ["npm", "ci", "--no-audit", "--no-fund"],
                    cwd=slides_dir,
                    check=True,
                    capture_output=True,
                    text=True,
                )
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def copy_template(dest: Path) -> Path:
    """汎用テンプレートを依存抜きで写し、依存は元の node_modules を指す。"""
    ensure_node_modules(TEMPLATE)
    shutil.copytree(TEMPLATE, dest, ignore=shutil.ignore_patterns("node_modules"))
    (dest / "node_modules").symlink_to(TEMPLATE / "node_modules", target_is_directory=True)
    return dest


def run_node(*args: str | Path, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["node", *map(str, args)], cwd=cwd, capture_output=True, text=True)


@dataclass
class Shape:
    kind: str  # sp / cxnSp / pic / graphicFrame
    x: float
    y: float
    w: float
    h: float
    text: str
    prst: str | None


@dataclass
class Slide:
    xml: str
    shapes: list[Shape]
    notes: str | None


def _inch(v: str) -> float:
    return round(int(v) / EMU, 2)


def _texts(el) -> str:
    paras = []
    for p in el.iter(f"{{{NS['a']}}}p"):
        paras.append("".join(t.text or "" for t in p.iter(f"{{{NS['a']}}}t")))
    return "\n".join(paras)


def _shapes(root) -> list[Shape]:
    out = []
    tree = root.find("p:cSld/p:spTree", NS)
    for el in list(tree):
        kind = el.tag.split("}")[1]
        if kind not in ("sp", "cxnSp", "pic", "graphicFrame"):
            continue
        off = el.find(".//a:off", NS)
        ext = el.find(".//a:ext[@cx]", NS)
        if off is None or ext is None:
            off = el.find(".//p:xfrm/a:off", NS)
            ext = el.find(".//p:xfrm/a:ext", NS)
        geom = el.find(".//a:prstGeom", NS)
        out.append(
            Shape(
                kind=kind,
                x=_inch(off.get("x")),
                y=_inch(off.get("y")),
                w=_inch(ext.get("cx")),
                h=_inch(ext.get("cy")),
                text=_texts(el),
                prst=geom.get("prst") if geom is not None else None,
            )
        )
    return out


def _notes_text(xml: str) -> str:
    root = ET.fromstring(xml)
    for sp in root.iter(f"{{{NS['p']}}}sp"):
        ph = sp.find(".//p:nvPr/p:ph", NS)
        if ph is not None and ph.get("type") == "body":
            return _texts(sp)
    return ""


def read_pptx(path: Path) -> dict:
    """版面（インチ）・ページ・書体名・色を返す。"""
    with zipfile.ZipFile(path) as z:
        pres = ET.fromstring(z.read("ppt/presentation.xml"))
        sz = pres.find("p:sldSz", NS)
        names = sorted(
            (n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
            key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[1]).group(1)),
        )
        slides = []
        for n in names:
            xml = z.read(n).decode("utf-8")
            rels_name = n.replace("slides/", "slides/_rels/") + ".rels"
            notes = None
            if rels_name in z.namelist():
                rels = z.read(rels_name).decode("utf-8")
                m = re.search(r'Target="\.\./notesSlides/(notesSlide\d+\.xml)"', rels)
                if m:
                    notes = _notes_text(z.read("ppt/notesSlides/" + m.group(1)).decode("utf-8"))
            slides.append(Slide(xml=xml, shapes=_shapes(ET.fromstring(xml)), notes=notes))
        media = [n for n in z.namelist() if n.startswith("ppt/media/")]
    all_xml = "".join(s.xml for s in slides)
    return {
        "size": (_inch(sz.get("cx")), _inch(sz.get("cy"))),
        "slides": slides,
        "fonts": set(re.findall(r'typeface="([^"]*)"', all_xml)),
        "colors": set(v.upper() for v in re.findall(r'srgbClr val="([0-9A-Fa-f]{6})"', all_xml)),
        "media": media,
    }


HEAD = re.compile(r"^##\s+(\d+)\.\s*(.*?)\s*$")


def script_readings(path: Path) -> dict[int, str]:
    """台本の読み上げをページ番号ごとに返す（`>` の行。`>` だけの行は段落の区切り）。"""
    pages: dict[int, list[str]] = {}
    cur: int | None = None
    para: list[str] = []

    def flush() -> None:
        if cur is not None and para:
            pages[cur].append("".join(para))
        para.clear()

    for line in path.read_text(encoding="utf-8").splitlines():
        m = HEAD.match(line)
        if m:
            flush()
            cur = int(m.group(1))
            pages[cur] = []
            continue
        if re.match(r"^#{1,2}\s", line):
            flush()
            cur = None
            continue
        if cur is None:
            continue
        q = re.match(r"^>\s?(.*)$", line)
        if q and q.group(1).strip():
            para.append(q.group(1).strip().replace("**", "").replace("`", ""))
        else:
            flush()
    flush()
    return {n: "\n".join(ps) for n, ps in pages.items()}
