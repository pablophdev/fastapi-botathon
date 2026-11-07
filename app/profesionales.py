import unicodedata


def normalizar_nombre(nombre):
    texto = unicodedata.normalize("NFKD", nombre or "")
    return " ".join("".join(c for c in texto if not unicodedata.combining(c)).casefold().split())


def catalogo_profesionales(vigencias):
    """Nombre más reciente informado en el año y todos sus institutos.

    Un contrato posterior sin nombre no borra uno ya conocido. Los homónimos
    permanecen separados por profesional_id; no se infiere identidad por nombre.
    """
    profesionales = {}
    for v in sorted(vigencias, key=lambda v: (v["desde"], v["datos"]["instituto"],
                                              v["datos"].get("nombre_profesional") or "")):
        datos = v["datos"]
        persona = profesionales.setdefault(datos["profesional_id"], {
            "profesional_id": datos["profesional_id"], "nombre_profesional": None, "institutos": set(),
        })
        persona["institutos"].add(datos["instituto"])
        if datos.get("nombre_profesional"):
            persona["nombre_profesional"] = datos["nombre_profesional"]
    return {clave: {**persona, "institutos": sorted(persona["institutos"])}
            for clave, persona in profesionales.items()}


def coincide_nombre(persona, nombre):
    return nombre is None or normalizar_nombre(nombre) in normalizar_nombre(persona["nombre_profesional"])
