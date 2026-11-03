/* MuseScore ESP32 Piano LED
   WS2812B: GPIO13, 176 LEDs, GRB. UDP: 4210.
   Wi-Fi portal: MuseScoreLED-XXXX / http://192.168.4.1
   Hold BOOT (GPIO0) for 5 seconds after startup to erase Wi-Fi.
*/
#include <WiFi.h>
#include <WiFiUdp.h>
#include <WebServer.h>
#include <Preferences.h>
#include <FastLED.h>

#define LED_PIN 13
#define LED_COUNT 176
#define RESET_PIN 0
#ifndef LED_BUILTIN
#define LED_BUILTIN 2
#endif
// Change to true if the onboard LED on your board is active-low.
#define ONBOARD_LED_ACTIVE_LOW false

CRGB leds[LED_COUNT];
WiFiUDP udp;
WebServer web(80);
Preferences prefs;
char packetBuffer[768];
String deviceName;
bool portalMode=false;
unsigned long bootPressedAt=0;
bool resetHandled=false;
const uint16_t UDP_PORT=4210;

void onboardLed(bool on){
  digitalWrite(LED_BUILTIN, ONBOARD_LED_ACTIVE_LOW ? !on : on);
}
void blinkOnboard(int times,int onMs=160,int offMs=160){
  for(int i=0;i<times;i++){
    onboardLed(true); delay(onMs);
    onboardLed(false); delay(offMs);
  }
}
void allOff(){ fill_solid(leds,LED_COUNT,CRGB::Black); FastLED.show(); }
void sendReply(const char* text){ udp.beginPacket(udp.remoteIP(),udp.remotePort()); udp.print(text); udp.endPacket(); }

String portalPage(){
  return String(
    "<!doctype html><html><head><meta charset='utf-8'>"
    "<meta name='viewport' content='width=device-width,initial-scale=1'>"
    "<title>MuseScore LED Wi-Fi</title>"
    "<style>"
    "*{box-sizing:border-box}body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;"
    "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:linear-gradient(135deg,#eef2ff,#ecfeff);color:#172033}"
    ".card{width:min(92vw,430px);background:rgba(255,255,255,.94);padding:30px;border-radius:20px;"
    "box-shadow:0 18px 50px rgba(30,64,175,.16);border:1px solid rgba(255,255,255,.8)}"
    ".icon{width:56px;height:56px;border-radius:16px;background:#2563eb;color:white;display:grid;place-items:center;"
    "font-size:28px;margin-bottom:18px}h1{font-size:24px;margin:0 0 8px}p{color:#64748b;line-height:1.5;margin:0 0 22px}"
    "label{display:block;font-size:13px;font-weight:650;margin:15px 0 7px}input{width:100%;padding:13px 14px;"
    "border:1px solid #cbd5e1;border-radius:11px;font-size:16px;outline:none}input:focus{border-color:#2563eb;"
    "box-shadow:0 0 0 3px rgba(37,99,235,.12)}button{width:100%;margin-top:22px;padding:13px;border:0;"
    "border-radius:11px;background:#2563eb;color:white;font-size:16px;font-weight:700;cursor:pointer}"
    ".foot{text-align:center;font-size:12px;color:#94a3b8;margin-top:18px}"
    "</style></head><body><main class='card'><div class='icon'>&#9835;</div>"
    "<h1>MuseScore ESP32 Piano LED</h1><p>Connect this device to your Wi-Fi network.<br>请输入需要连接的 Wi-Fi。</p>"
    "<form method='post' action='/save'><label>Wi-Fi name / SSID</label><input name='ssid' required autocomplete='ssid' placeholder='Your Wi-Fi name'>"
    "<label>Password / 密码</label><input name='pass' type='password' autocomplete='current-password' placeholder='Wi-Fi password'>"
    "<button type='submit'>Save and restart / 保存并重启</button></form>"
    "<div class='foot'>UDP port 4210</div></main></body></html>"
  );
}

String savedPage(){
 return String("<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>")+
 "<style>body{font-family:-apple-system,'Segoe UI',sans-serif;background:#eff6ff;text-align:center;padding:60px 20px;color:#172033}"+
 ".box{max-width:420px;margin:auto;background:white;padding:32px;border-radius:18px;box-shadow:0 15px 40px #bfdbfe}"+
 "h2{color:#15803d}</style><div class='box'><h2>Saved successfully</h2><p>Wi-Fi 已保存，设备正在重启...</p></div>";
}

void startPortal(){
 portalMode=true; onboardLed(false); WiFi.mode(WIFI_AP); WiFi.softAP(deviceName.c_str());
 web.on("/",HTTP_GET,[](){web.send(200,"text/html; charset=utf-8",portalPage());});
 web.on("/save",HTTP_POST,[](){
   String ssid=web.arg("ssid"),password=web.arg("pass");
   if(!ssid.length()){web.send(400,"text/plain","SSID required");return;}
   prefs.begin("wifi",false); prefs.putString("ssid",ssid); prefs.putString("pass",password); prefs.end();
   web.send(200,"text/html; charset=utf-8",savedPage());
   blinkOnboard(3); delay(250); ESP.restart();
 });
 web.begin(); Serial.println("AP: "+deviceName+" / http://192.168.4.1");
}

bool connectSavedWiFi(){
 prefs.begin("wifi",true); String ssid=prefs.getString("ssid",""); String pass=prefs.getString("pass",""); prefs.end();
 if(!ssid.length()) return false;
 WiFi.mode(WIFI_STA); WiFi.begin(ssid.c_str(),pass.c_str());
 unsigned long start=millis();
 while(WiFi.status()!=WL_CONNECTED && millis()-start<15000){onboardLed(true);delay(150);onboardLed(false);delay(150);}
 return WiFi.status()==WL_CONNECTED;
}

void handlePacket(char* cmd){
 if(!strcmp(cmd,"DISCOVER_MUSESCORE_LED")){String r="MUSESCORE_LED,"+deviceName+","+WiFi.localIP().toString()+",4210,176";sendReply(r.c_str());return;}
 if(!strcmp(cmd,"A")){allOff();sendReply("OK,ALL_OFF");return;}
 if(!strcmp(cmd,"P")){sendReply("PONG");return;}
 if(cmd[0]=='B'){int v;if(sscanf(cmd,"B,%d",&v)==1){FastLED.setBrightness(constrain(v,0,255));FastLED.show();}return;}
 if(cmd[0]=='L'){int i,r,g,b;if(sscanf(cmd,"L,%d,%d,%d,%d",&i,&r,&g,&b)==4&&i>=0&&i<LED_COUNT){leds[i]=CRGB(r,g,b);FastLED.show();}return;}
 if(cmd[0]=='F'){
   char* p=strchr(cmd+2,','); if(!p)return; p++;
   fill_solid(leds,LED_COUNT,CRGB::Black);
   for(char* t=strtok(p,";");t;t=strtok(nullptr,";")){int i,r,g,b;if(sscanf(t,"%d:%d:%d:%d",&i,&r,&g,&b)==4&&i>=0&&i<LED_COUNT)leds[i]=CRGB(r,g,b);}
   FastLED.show();
 }
}

void checkBootReset(){
 bool pressed=digitalRead(RESET_PIN)==LOW;
 if(pressed&&!bootPressedAt){bootPressedAt=millis();resetHandled=false;}
 if(!pressed){bootPressedAt=0;resetHandled=false;}
 if(pressed&&!resetHandled&&bootPressedAt&&millis()-bootPressedAt>=5000){
   resetHandled=true; prefs.begin("wifi",false);prefs.clear();prefs.end();
   blinkOnboard(5); delay(250); ESP.restart();
 }
}

void setup(){
 Serial.begin(115200);
 pinMode(LED_BUILTIN,OUTPUT); onboardLed(false);
 pinMode(RESET_PIN,INPUT_PULLUP);
 FastLED.addLeds<WS2812B,LED_PIN,GRB>(leds,LED_COUNT); FastLED.setBrightness(80); allOff();
 char suffix[5];snprintf(suffix,sizeof(suffix),"%04X",(uint16_t)(ESP.getEfuseMac()&0xffff));deviceName=String("MuseScoreLED-")+suffix;
 if(!connectSavedWiFi()){startPortal();return;}
 udp.begin(UDP_PORT);Serial.println("ESP32 IP: "+WiFi.localIP().toString());blinkOnboard(2);onboardLed(true);
}
void loop(){
 if(portalMode){web.handleClient();delay(2);return;}
 checkBootReset();
 int size=udp.parsePacket();
 if(size){int n=udp.read(packetBuffer,sizeof(packetBuffer)-1);if(n>0){packetBuffer[n]=0;handlePacket(packetBuffer);}}
}
