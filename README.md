# mct7atest-iot

Plataforma IoT con ESP32-C3 Super Mini, API HTTPS en FastAPI y panel web/móvil.

## Estructura
- `backend/`: API FastAPI (`main.py`), panel web (`dashboard.html`) y `requirements.txt`. Desplegado en `https://mct7atest.maewalliscorp.org` (systemd `mct7atest.service` en puerto interno `8010` detrás de Nginx).
- `firmware/esp32-c3-supermini/`: Firmware Arduino (`esp32-c3-supermini.ino`) con `WiFiManager`, `Preferences` (NVS), emparejamiento por código de 6 dígitos, envío HTTPS con `X-Device-Token` y control bidireccional del LED azul (GPIO 8).
- `web/`: Reservado para el frontend escalable en Vite + React.
- `mobile/`: Reservado para la app móvil en React Native / Expo.

## Control Bidireccional (LED Azul · GPIO 8)
1. **Panel Web (`dashboard.html`)**:
   - Cada dispositivo en la tabla muestra un botón interactivo para alternar el LED (Encendido / Apagado).
   - Realiza una petición `POST /api/admin/dispositivos/{mac}/led` con payload `{"estado": true/false}`.
   - Si el dispositivo aún no ha confirmado la ejecución en hardware, muestra el indicador `⏳ aplicando...`.
2. **Backend FastAPI (`main.py`)**:
   - Almacena en SQLite (`dispositivos`) el estado `led_deseado` y el estado reportado `led_actual`.
   - Cuando el ESP32 envía sus lecturas periódicas a `POST /api/lecturas`, la respuesta HTTPS le devuelve el comando actual `{"ok": true, "mac": "...", "led_deseado": 0|1}`.
   - El historial de lecturas (`/api/admin/lecturas`) también registra el estado del LED reportado por la placa.
3. **Firmware ESP32-C3 Super Mini (`.ino`)**:
   - Configura `PIN_LED = 8` como salida.
   - Variable `LED_ACTIVO_BAJO = true` (en la mayoría de placas Super Mini, nivel `LOW` enciende el LED azul).
   - En cada ciclo de envío, reporta su `led_estado` y lee `led_deseado` de la respuesta HTTPS para conmutar inmediatamente el pin si hubo cambios.

## Flujo de trabajo
- Editar código localmente en Antigravity IDE.
- Ejecutar `./deploy.sh` para publicar cambios de `backend/` en el VPS.
