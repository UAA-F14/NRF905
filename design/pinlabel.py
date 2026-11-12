#!/usr/bin/env python3
"""
pinlabel.py - Coloca una etiqueta local en cada pin de un simbolo (p. ej. U1).

Uso:
  python pinlabel.py <esquematico.kicad_sch> [REF] [all|unconnected] [name|var]

  REF   referencia del simbolo (default U1)
  modo  all           -> todos los pines
        unconnected   -> omite pines donde ya hay un cable/etiqueta
  texto name          -> la etiqueta es el nombre del pin  (PA0)
        var           -> la etiqueta es ${REF:PIN_NAME(num)}

Correcciones respecto a la version anterior:
  * Eje Y invertido (lib: Y arriba; esquematico: Y abajo)  -> y = y_sym - y_pin
  * Se aplican espejo (mirror x / y) y rotacion del simbolo
  * Solo se usan los pines de la unidad colocada (+ unidad 0 comun)
  * El numero de pin se lee de (number "..."), no de un token suelto
  * El texto de la etiqueta va entre comillas (obligatorio en .kicad_sch)
  * El angulo de la etiqueta apunta hacia afuera del simbolo
"""
import math
import os
import shutil
import sys
import uuid


# ----------------------------------------------------------------------------
# Parser S-expression (tokens de texto conservan sus comillas)
# ----------------------------------------------------------------------------
def parse_sexpr(content):
    stack, root = [], None
    cur = []
    in_str = esc = False

    def flush():
        nonlocal cur
        if cur:
            stack[-1].append("".join(cur))
            cur = []

    for ch in content:
        if in_str:
            cur.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            cur.append(ch)
        elif ch == "(":
            flush()
            node = []
            if stack:
                stack[-1].append(node)
            else:
                root = node
            stack.append(node)
        elif ch == ")":
            flush()
            if stack:
                stack.pop()
        elif ch.isspace():
            flush()
        else:
            cur.append(ch)
    return root


def to_str(node, indent=0):
    if not isinstance(node, list):
        return str(node)
    if all(not isinstance(x, list) for x in node):
        return "(" + " ".join(str(x) for x in node) + ")"
    pad = "\t" * indent
    out = "(" + str(node[0])
    inline = True
    for item in node[1:]:
        if isinstance(item, list):
            out += "\n" + pad + "\t" + to_str(item, indent + 1)
            inline = False
        else:
            out += " " + str(item) if inline else "\n" + pad + "\t" + str(item)
    return out + "\n" + pad + ")"


def unq(s):
    s = str(s)
    return s[1:-1] if len(s) >= 2 and s[0] == s[-1] == '"' else s


def find(node, key):
    for sub in node:
        if isinstance(sub, list) and sub and sub[0] == key:
            return sub
    return None


def find_all(node, key):
    return [s for s in node if isinstance(s, list) and s and s[0] == key]


# ----------------------------------------------------------------------------
# Geometria
# ----------------------------------------------------------------------------
def rot_ccw(x, y, deg):
    """Rotacion CCW en ejes Y-arriba, exacta para multiplos de 90."""
    d = int(round(deg)) % 360
    if d == 0:
        return x, y
    if d == 90:
        return -y, x
    if d == 180:
        return -x, -y
    if d == 270:
        return y, -x
    r = math.radians(deg)
    return x * math.cos(r) - y * math.sin(r), x * math.sin(r) + y * math.cos(r)


def fmt(v):
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


# ----------------------------------------------------------------------------
def collect_pins(libsym, unit, body_style):
    """Pines (num, name, x, y, angulo) de la unidad colocada, coords de libreria."""
    pins = []
    base = unq(libsym[1]).split(":")[-1]
    for sub in find_all(libsym, "symbol"):
        # nombre: <base>_<unidad>_<estilo>
        parts = unq(sub[1]).rsplit("_", 2)
        try:
            u, s = int(parts[-2]), int(parts[-1])
        except (ValueError, IndexError):
            u, s = 0, 0
        if u not in (0, unit) or s not in (0, body_style):
            continue
        for pin in find_all(sub, "pin"):
            at = find(pin, "at")
            name = find(pin, "name")
            num = find(pin, "number")
            if not (at and name and num):
                continue
            pins.append(
                (
                    unq(num[1]),
                    unq(name[1]),
                    float(at[1]),
                    float(at[2]),
                    float(at[3]) if len(at) > 3 else 0.0,
                )
            )
    return pins


def process(path, target_ref="U1", mode="all", text_mode="name"):
    with open(path, "r", encoding="utf-8") as f:
        root = parse_sexpr(f.read())

    libs = {}
    lib_node = find(root, "lib_symbols")
    for s in find_all(lib_node or [], "symbol"):
        libs[unq(s[1])] = s

    inst = None
    for s in find_all(root, "symbol"):
        ref = None
        for p in find_all(s, "property"):
            if unq(p[1]) == "Reference":
                ref = unq(p[2])
        if ref != target_ref:
            continue
        at = find(s, "at")
        mir = find(s, "mirror")
        unit = find(s, "unit")
        conv = find(s, "body_style") or find(s, "convert")
        inst = dict(
            lib_id=unq(find(s, "lib_id")[1]),
            x=float(at[1]),
            y=float(at[2]),
            rot=float(at[3]) if len(at) > 3 else 0.0,
            mirror=unq(mir[1]) if mir else "",
            unit=int(unit[1]) if unit else 1,
            body=int(conv[1]) if conv else 1,
        )
        break

    if not inst:
        sys.exit(f"Error: no se encontro el simbolo '{target_ref}'.")
    if inst["lib_id"] not in libs:
        sys.exit(f"Error: no hay definicion de '{inst['lib_id']}' en lib_symbols.")

    print(
        f"{target_ref}: {inst['lib_id']} en ({inst['x']}, {inst['y']}) "
        f"rot={inst['rot']} mirror='{inst['mirror']}' unidad={inst['unit']}"
    )

    # puntos ya ocupados (modo unconnected)
    used = set()
    if mode == "unconnected":
        for w in find_all(root, "wire"):
            for pt in find_all(find(w, "pts"), "xy"):
                used.add((round(float(pt[1]), 2), round(float(pt[2]), 2)))
        for key in ("label", "global_label", "hierarchical_label"):
            for l in find_all(root, key):
                a = find(l, "at")
                used.add((round(float(a[1]), 2), round(float(a[2]), 2)))

    new_labels, skipped = [], 0
    for num, name, px, py, pang in collect_pins(libs[inst["lib_id"]], inst["unit"], inst["body"]):
        if name in ("~", ""):
            continue
        # 1) espejo (en ejes de libreria, Y arriba)
        if inst["mirror"] == "x":
            py, pang = -py, (-pang) % 360
        elif inst["mirror"] == "y":
            px, pang = -px, (180 - pang) % 360
        # 2) rotacion CCW
        rx, ry = rot_ccw(px, py, inst["rot"])
        ang = (pang + inst["rot"]) % 360
        # 3) pasar a coordenadas de esquematico (Y hacia abajo)
        ax, ay = inst["x"] + rx, inst["y"] - ry

        if (round(ax, 2), round(ay, 2)) in used:
            skipped += 1
            continue

        # direccion hacia el cuerpo = ang (Y arriba). La etiqueta va hacia afuera.
        out = (ang + 180) % 360
        label_ang = {0: 0, 90: 90, 180: 180, 270: 270}.get(int(round(out)) % 360, 0)
        just = "right" if label_ang in (180, 270) else "left"

        text = name if text_mode == "name" else "${%s:PIN_NAME(%s)}" % (target_ref, num)
        new_labels.append(
            [
                "label",
                '"%s"' % text.replace("\\", "\\\\").replace('"', '\\"'),
                ["at", fmt(ax), fmt(ay), str(label_ang)],
                ["effects", ["font", ["size", "1.27", "1.27"]], ["justify", just, "bottom"]],
                ["uuid", '"%s"' % uuid.uuid4()],
            ]
        )

    root.extend(new_labels)

    shutil.copy2(path, path + ".bak")
    with open(path, "w", encoding="utf-8") as f:
        f.write(to_str(root) + "\n")
    print(f"Listo: {len(new_labels)} etiquetas anadidas ({skipped} omitidas). Respaldo: {path}.bak")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    process(
        sys.argv[1],
        sys.argv[2] if len(sys.argv) > 2 else "U1",
        sys.argv[3] if len(sys.argv) > 3 else "all",
        sys.argv[4] if len(sys.argv) > 4 else "name",
    )
