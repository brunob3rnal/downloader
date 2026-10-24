"""Marcadores `slow` y `ytdlp_internals` (T067).

`downloader actualizar` ejecuta los tests marcados `ytdlp_internals` antes de aceptar una
versión nueva de yt-dlp (FR-025): un test que pierda el marcador dejaría sin verificar el
comportamiento interno que prueba. `slow` separa los tests de más de unos 5 s.
"""

import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]

# Los que dependen de cómo funciona yt-dlp por dentro, no solo de su interfaz pública.
INTERNOS_YTDLP = {
    # R1: HLS con el descargador nativo (y AES-128 con pycryptodomex), no con ffmpeg.
    "tests/unit/test_ytdl.py::test_crear_ydl_prefiere_hls_nativo",
    "tests/integration/test_hls_aes.py::test_hls_aes_128_se_descarga_y_descifra",
    # R4: registro de la última petición (urlopen, dl, descargador externo).
    "tests/unit/test_ytdl.py::test_el_registro_empieza_vacio",
    "tests/unit/test_ytdl.py::test_dl_registra_fase_de_descarga_y_descargador_externo",
    "tests/integration/test_registro_peticiones.py::test_corte_repetido_es_temporal",
    "tests/integration/test_registro_peticiones.py::test_503_repetido_es_temporal",
    "tests/integration/test_registro_peticiones.py::test_fragmento_hls_ausente_es_permanente",
    "tests/integration/test_registro_peticiones.py::test_429_con_causa_es_temporal",
    "tests/integration/test_registro_peticiones.py::test_429_sin_causa_es_temporal",
    "tests/integration/test_registro_peticiones.py::"
    "test_token_hls_caducado_se_recupera_al_volver_a_extraer",
    "tests/integration/test_registro_peticiones.py::"
    "test_403_constante_falla_tras_3_intentos_sin_avance",
    "tests/integration/test_registro_peticiones.py::test_403_con_avance_no_agota_el_tope",
    # R19: respuestas de la extracción que quedan abiertas.
    "tests/unit/test_ytdl.py::test_dl_cierra_las_respuestas_abiertas_al_extraer",
    "tests/unit/test_ytdl.py::test_una_respuesta_ya_cerrada_no_da_error",
    "tests/unit/test_ytdl.py::test_no_toca_las_respuestas_abiertas_por_la_descarga",
    "tests/unit/test_ytdl.py::test_close_cierra_las_respuestas_de_una_extraccion_sin_descarga",
    "tests/integration/test_crash_recovery.py::test_cierre_abrupto_a_mitad_y_relanzar",
    # R23: HTTP 416 al reanudar.
    "tests/integration/test_rango_no_satisfacible.py::"
    "test_parcial_completo_con_416_se_publica_sin_pedir_mas_bytes",
    "tests/integration/test_rango_no_satisfacible.py::"
    "test_parcial_completo_pero_danado_no_pasa_la_verificacion",
    "tests/integration/test_rango_no_satisfacible.py::"
    "test_parcial_mayor_que_el_archivo_se_reinicia_con_aviso",
    "tests/integration/test_rango_no_satisfacible.py::"
    "test_416_sin_content_range_se_reintenta_como_ambiguo",
    "tests/integration/test_rango_no_satisfacible.py::"
    "test_416_sin_content_range_persistente_falla_tras_3_intentos_sin_avance",
    # R24: el `.part` que deja abierto un fragmento fallido.
    "tests/integration/test_integridad.py::"
    "test_cada_intento_empieza_sin_archivos_del_anterior_abiertos",
    # R3: yt-dlp borra el `.part` si el servidor ignora `Range`.
    "tests/integration/test_sin_reanudacion.py::test_reinicio_dentro_de_la_misma_llamada",
    "tests/integration/test_sin_reanudacion.py::test_reinicio_en_un_intento_posterior",
    "tests/integration/test_sin_reanudacion.py::test_sin_aviso_si_el_servidor_admite_range",
    # R21: el vigilante lee con `_real_read`/`read1` de la respuesta de yt-dlp.
    "tests/integration/test_estancamiento.py::test_conexion_abierta_sin_datos",
    "tests/integration/test_estancamiento.py::test_conexion_a_goteo",
    # R25: cadena de excepciones de yt-dlp al leer las cookies del navegador.
    "tests/unit/test_ytdl.py::test_cookies_ilegibles_indican_el_navegador_y_la_causa",
    "tests/unit/test_ytdl.py::test_los_demas_errores_con_cookies_no_se_confunden",
    # R5: el manifiesto pasa por `urlopen` al extraer y al descargar, `process_info` antes de
    # pedir nada, la clave AES-128 con `ydl.urlopen` y `hls_aes`, y la delegación en ffmpeg.
    "tests/integration/test_directo_drm.py::"
    "test_hls_con_drm_rechazado_sin_pedir_clave_ni_fragmentos",
    "tests/integration/test_directo_drm.py::test_session_key_del_maestro_con_widevine",
    "tests/integration/test_directo_drm.py::"
    "test_manifiesto_entregado_por_el_extractor_con_widevine",
    "tests/integration/test_directo_drm.py::test_metodo_no_soportado_con_clave_identity",
    "tests/integration/test_hls_aes.py::test_aes_128_identity_se_descarga_y_descifra",
    "tests/integration/test_hls_aes.py::test_aes_128_con_la_clave_en_una_uri_data",
    "tests/integration/test_hls_aes.py::test_aes_128_con_la_clave_del_extractor",
    "tests/integration/test_hls_aes.py::test_hls_sin_cifrar_sigue_descargandose",
    "tests/integration/test_hls_aes.py::test_la_clave_se_pide_con_la_sesion_del_usuario",
    "tests/integration/test_hls_aes.py::test_clave_data_con_base64_invalido_falla_sin_excepcion",
    # R18: `HttpFD` escribe en el `.part` lo que devuelve `read` antes de volver a leer, y ningún
    # `except KeyboardInterrupt` del camino de descarga descarta Ctrl+C.
    "tests/integration/test_interrupcion.py::test_ctrl_c_a_mitad_de_bloque_no_pierde_lo_recibido",
    "tests/integration/test_interrupcion.py::"
    "test_ctrl_c_con_los_ultimos_bytes_y_relanzar_publica_sin_volver_a_descargar",
    # R5 (DASH, ISM y F4M): los extractores, también el genérico (que lee 512 bytes antes que
    # el resto), leen el manifiesto con `urlopen(...).read()` y crean los formatos solo a partir
    # de esos bytes, un formato por `Representation`; la descarga DASH no vuelve a pedir el MPD.
    "tests/integration/test_directo_drm.py::"
    "test_mpd_con_el_video_protegido_se_rechaza_sin_descargar_el_audio",
    "tests/integration/test_directo_drm.py::"
    "test_mpd_con_el_video_protegido_y_calidad_audio_descarga_solo_el_audio",
    "tests/integration/test_directo_drm.py::test_proteccion_en_el_period_o_en_el_mpd_rechaza_todo",
    "tests/integration/test_directo_drm.py::test_mp4protection_solo_cuenta_como_proteccion",
    "tests/integration/test_directo_drm.py::"
    "test_mpd_mixto_descarga_la_720p_sin_pedir_la_1080p_protegida",
    "tests/integration/test_directo_drm.py::test_aviso_en_plural_con_dos_representaciones_omitidas",
    "tests/integration/test_directo_drm.py::"
    "test_mpd_mixto_por_url_directa_con_el_extractor_generico",
    "tests/integration/test_directo_drm.py::"
    "test_mpd_sin_proteccion_se_descarga_integro_y_sin_aviso",
    "tests/integration/test_directo_drm.py::test_ism_con_proteccion_se_rechaza",
    "tests/integration/test_directo_drm.py::test_f4m_con_drm_se_rechaza_entero",
    # R5 (formatos marcados por el extractor, H1): yt-dlp descarta los formatos `has_drm`, lo
    # resume en `_has_drm` y entrega a `process_info` la selección final (`requested_formats`).
    "tests/integration/test_directo_drm.py::"
    "test_video_marcado_has_drm_se_rechaza_sin_descargar_el_audio",
    "tests/integration/test_directo_drm.py::"
    "test_video_marcado_has_drm_con_calidad_audio_descarga_el_audio",
    "tests/integration/test_directo_drm.py::test_solo_audio_sin_has_drm_se_descarga",
    "tests/integration/test_directo_drm.py::test_vcodec_desconocido_sin_has_drm_se_descarga",
}

LENTOS = {
    "tests/integration/test_interrupcion.py::test_ctrl_c_guarda_el_progreso_y_relanzar_termina",
    "tests/integration/test_interrupcion.py::test_ctrl_c_a_mitad_de_bloque_no_pierde_lo_recibido",
    "tests/integration/test_crash_recovery.py::test_cierre_abrupto_a_mitad_y_relanzar",
    "tests/integration/test_network_cuts.py::test_servidor_colgado_es_temporal",
    "tests/unit/test_worker_espera.py::test_nunca_falla_por_errores_temporales_no_ambiguos",
}


def recogidos(expresion):
    """Ids de los tests que selecciona `-m expresion`, sin los parámetros."""
    resultado = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
         "-m", expresion],
        cwd=RAIZ, capture_output=True, text=True,
    )
    assert resultado.returncode in (0, 5), resultado.stdout + resultado.stderr  # 5: ninguno
    return {linea.split("[")[0] for linea in resultado.stdout.splitlines() if "::" in linea}


def test_marcados_ytdlp_internals():
    assert recogidos("ytdlp_internals") == INTERNOS_YTDLP


def test_marcados_slow():
    assert recogidos("slow") == LENTOS
