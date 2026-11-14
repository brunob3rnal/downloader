"""Ventana falsa para probar la aplicación sin pywebview (specs/002-desktop-app, research R19).

Implementa las cuatro operaciones que la lógica pide a la ventana y registra cada llamada en
`llamadas`, en orden, como `(operación, argumentos)`.
"""


class VentanaFalsa:
    def __init__(self, carpeta=None, con_dialogo=True):
        self.carpeta = carpeta  # lo que devuelve `examinar_carpeta`; `None` = el usuario cancela
        self.con_dialogo = con_dialogo  # `False`: sin diálogo de carpeta (`api/examinar` → 501)
        self.llamadas: list[tuple[str, tuple]] = []
        self.destruida = False

    def examinar_carpeta(self):
        self.llamadas.append(("examinar_carpeta", ()))
        return self.carpeta

    def enfocar(self):
        self.llamadas.append(("enfocar", ()))

    def ejecutar_js(self, codigo):
        self.llamadas.append(("ejecutar_js", (codigo,)))

    def destruir(self):
        self.llamadas.append(("destruir", ()))
        self.destruida = True

    def operaciones(self) -> list[str]:
        return [nombre for nombre, _ in self.llamadas]
