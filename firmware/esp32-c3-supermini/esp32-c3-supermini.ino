#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <WiFiManager.h>
#include <Preferences.h>

Preferences prefs;

const char* BASE_URL = "https://mct7atest.maewalliscorp.org";
String deviceToken = "";
String savedSsid = "";
String savedPass = "";
String pendingCode = "";

const int BTN_BOOT = 9; // Botón BOOT en la ESP32-C3 Super Mini
const int PIN_LED = 8;  // LED azul integrado en GPIO 8

// En la mayoría de las placas ESP32-C3 Super Mini, el LED azul se enciende con nivel BAJO (LOW).
// Si tu placa enciende el LED al revés, cambia esta constante a false.
const bool LED_ACTIVO_BAJO = true;
int estadoLedActual = 0; // 0 = apagado, 1 = encendido

unsigned long ultimoEnvio = 0;
const unsigned long INTERVALO_MS = 5000;

void aplicarLed(int estado) {
  estadoLedActual = (estado != 0) ? 1 : 0;
  if (LED_ACTIVO_BAJO) {
    digitalWrite(PIN_LED, estadoLedActual ? LOW : HIGH);
  } else {
    digitalWrite(PIN_LED, estadoLedActual ? HIGH : LOW);
  }
}

// Lectura de temperatura del sensor interno integrado en el silicio del ESP32-C3.
// Nota: Al medir la temperatura del procesador, suele registrar entre 35°C y 55°C debido al calor del chip y Wi-Fi.
// Si se conecta un sensor externo ambiental (ej. DHT22 o DS18B20), se puede sustituir aquí.
float leerTemperatura() {
  float t = temperatureRead();
  if (isnan(t) || t < -40.0 || t > 125.0) {
    return 0.0;
  }
  return round(t * 10.0) / 10.0;
}

void cargarConfig() {
  prefs.begin("iot-v2", true);
  deviceToken = prefs.getString("token", "");
  savedSsid   = prefs.getString("ssid", "");
  savedPass   = prefs.getString("pass", "");
  pendingCode = prefs.getString("pcode", "");
  prefs.end();
  savedSsid.trim();
}

void guardarPortalConfig(const String& ssid, const String& pass, const String& code) {
  prefs.begin("iot-v2", false);
  if (ssid.length() > 0) {
    prefs.putString("ssid", ssid);
    prefs.putString("pass", pass);
  }
  if (code.length() == 6) {
    prefs.putString("pcode", code);
  }
  prefs.end();
}

void guardarToken(const String& token) {
  prefs.begin("iot-v2", false);
  prefs.putString("token", token);
  prefs.remove("pcode");
  prefs.end();
  deviceToken = token;
  pendingCode = "";
}

void limpiarCodigoPendiente() {
  prefs.begin("iot-v2", false);
  prefs.remove("pcode");
  prefs.end();
  pendingCode = "";
}

void borrarTodo() {
  prefs.begin("iot-v2", false);
  prefs.clear();
  prefs.end();
  WiFiManager wm;
  wm.resetSettings();
  Serial.println("Reseteo de fábrica completado. Reiniciando...");
  delay(1000);
  ESP.restart();
}

bool conectarWifiSTA(int timeoutSeg = 15) {
  if (savedSsid.length() == 0) return false;

  WiFi.mode(WIFI_STA);
  WiFi.begin(savedSsid.c_str(), savedPass.c_str());
  WiFi.setTxPower(WIFI_POWER_8_5dBm);

  Serial.print("Conectando en modo STA limpio a " + savedSsid + " (pass len=" + String(savedPass.length()) + ")");
  unsigned long inicio = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - inicio < (unsigned long)timeoutSeg * 1000) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("Conectado! IP: " + WiFi.localIP().toString());
    return true;
  }
  Serial.println("No se pudo conectar a " + savedSsid);
  return false;
}

String extraerCampoJson(const String& json, const char* clave) {
  String patron = "\"" + String(clave) + "\":\"";
  int inicio = json.indexOf(patron);
  if (inicio < 0) return "";
  inicio += patron.length();
  int fin = json.indexOf("\"", inicio);
  if (fin < 0) return "";
  return json.substring(inicio, fin);
}

int extraerEnteroJson(const String& json, const char* clave, int defecto = -1) {
  String patron = "\"" + String(clave) + "\":";
  int inicio = json.indexOf(patron);
  if (inicio < 0) return defecto;
  inicio += patron.length();
  while (inicio < json.length() && (json[inicio] == ' ' || json[inicio] == '\"')) {
    inicio++;
  }
  if (inicio >= json.length()) return defecto;
  if (json.substring(inicio).startsWith("true")) return 1;
  if (json.substring(inicio).startsWith("false")) return 0;
  return json.substring(inicio).toInt();
}

bool activarConCodigo(const String& codigo) {
  WiFiClientSecure client;
  client.setInsecure();

  HTTPClient http;
  http.begin(client, String(BASE_URL) + "/api/dispositivos/activar");
  http.addHeader("Content-Type", "application/json");

  String payload = "{\"mac\":\"" + WiFi.macAddress() + "\",\"codigo\":\"" + codigo + "\",\"nombre\":\"esp32-c3-supermini\"}";
  int code = http.POST(payload);
  String body = http.getString();
  http.end();

  Serial.printf("Activación HTTP %d -> %s\n", code, body.c_str());
  if (code == 200) {
    String token = extraerCampoJson(body, "device_token");
    if (token.length() > 10) {
      guardarToken(token);
      Serial.println("Token único guardado en NVS!");
      return true;
    }
  }
  return false;
}

void abrirPortal() {
  WiFiManager wm;
  wm.setBreakAfterConfig(true);
  wm.setConnectTimeout(1);
  wm.setConfigPortalTimeout(180);

  WiFiManagerParameter pCode("pairing_code", "Código de 6 dígitos (solo placa nueva)", "", 10);
  wm.addParameter(&pCode);

  Serial.println("Abriendo portal ESP32-Config (clave: 12345678)...");
  wm.startConfigPortal("ESP32-Config", "12345678");

  // Leer directo del formulario en RAM (false = no leer de NVS vieja)
  String nuevoSsid = wm.getWiFiSSID(false);
  if (nuevoSsid.length() == 0) nuevoSsid = wm.getWiFiSSID(true);
  nuevoSsid.trim();

  String nuevoPass = wm.getWiFiPass(false);
  if (nuevoPass.length() == 0) nuevoPass = wm.getWiFiPass(true);

  String codigo = String(pCode.getValue());
  codigo.trim();

  Serial.println("Guardando -> SSID: " + nuevoSsid + " | Pass len: " + String(nuevoPass.length()) + " | Código: " + codigo);
  guardarPortalConfig(nuevoSsid, nuevoPass, codigo);

  Serial.println("Reiniciando para conectar en modo STA limpio...");
  delay(800);
  ESP.restart();
}

void setup() {
  Serial.begin(115200);
  delay(1500);
  pinMode(BTN_BOOT, INPUT_PULLUP);
  pinMode(PIN_LED, OUTPUT);
  aplicarLed(0); // Inicia con el LED apagado

  cargarConfig();

  if (savedSsid.length() > 0 && (deviceToken.length() > 0 || pendingCode.length() == 6)) {
    if (conectarWifiSTA(15)) {
      if (deviceToken.length() == 0 && pendingCode.length() == 6) {
        if (!activarConCodigo(pendingCode)) {
          Serial.println("Código inválido o expirado. Abriendo portal...");
          limpiarCodigoPendiente();
          abrirPortal();
          return;
        }
      }
      Serial.println("Listo! MAC: " + WiFi.macAddress());
      return;
    }
  }

  abrirPortal();
}

void loop() {
  // BOOT 3s = cambiar Wi-Fi (conserva token) | BOOT 8s = reseteo de fábrica
  if (digitalRead(BTN_BOOT) == LOW) {
    unsigned long inicio = millis();
    while (digitalRead(BTN_BOOT) == LOW) {
      if (millis() - inicio > 8000) {
        borrarTodo();
      }
      delay(50);
    }
    if (millis() - inicio > 3000) {
      Serial.println("Cambiando Wi-Fi (conservando token del dispositivo)...");
      abrirPortal();
      return;
    }
  }

  if (millis() - ultimoEnvio >= INTERVALO_MS) {
    ultimoEnvio = millis();

    if (WiFi.status() == WL_CONNECTED && deviceToken.length() > 0) {
      WiFiClientSecure client;
      client.setInsecure();

      HTTPClient http;
      http.begin(client, String(BASE_URL) + "/api/lecturas");
      http.addHeader("Content-Type", "application/json");
      http.addHeader("X-Device-Token", deviceToken);

      // Enviamos telemetría con la temperatura real y el estado del LED
      float tempActual = leerTemperatura();
      String payload = "{\"temperatura\":" + String(tempActual, 1) + ",\"wifi_rssi\":" + String(WiFi.RSSI()) + ",\"led_estado\":" + String(estadoLedActual) + "}";
      int code = http.POST(payload);

      if (code > 0) {
        String resp = http.getString();
        Serial.printf("HTTPS %d -> %s\n", code, resp.c_str());

        // Procesar comando de downlink (control bidireccional)
        if (code == 200) {
          int ledDeseado = extraerEnteroJson(resp, "led_deseado");
          if (ledDeseado == 0 || ledDeseado == 1) {
            if (ledDeseado != estadoLedActual) {
              Serial.printf("[BIDIRECCIONAL] Comando recibido: Cambiando LED a %s\n", ledDeseado ? "ENCENDIDO" : "APAGADO");
              aplicarLed(ledDeseado);
            }
          }
        }
      } else {
        Serial.printf("Error HTTPS (%d): %s\n", code, http.errorToString(code).c_str());
      }
      http.end();
    } else if (WiFi.status() != WL_CONNECTED) {
      conectarWifiSTA(10);
    }
  }
}
