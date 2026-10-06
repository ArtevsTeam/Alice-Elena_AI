[app]
title = Elena Music
package.name = elena
package.domain = org.elena

source.dir = .
source.include_exts = py,png,jpg,kv,atlas,json,html,css,js,ttf,pem,bin,onnx,txt,ogg,wav,mdl
source.exclude_dirs = tests,bin,.buildozer,__pycache__,certs,cache,.git,models/vosk-ru-0.42
source.exclude_patterns = *.pyc,*.pyo,*.so,buildozer.spec.bak

version = 1.0.0

requirements = python3,kivy==2.2.1,\
    fastapi,uvicorn,pydantic,starlette,\
    requests,urllib3,certifi,idna,charset-normalizer,\
    yandex-music,soundfile,numpy,\
    cryptography,pyjnius,pillow,android

android.permissions = INTERNET,RECORD_AUDIO,WAKE_LOCK,FOREGROUND_SERVICE,\
    ACCESS_NETWORK_STATE,ACCESS_WIFI_STATE,CHANGE_WIFI_MULTICAST_STATE

android.api = 33
android.minapi = 26
android.ndk = 25b
android.archs = arm64-v8a, armeabi-v7a

android.allow_backup = True
android.presplash_color = #0a0a0d
android.orientation = landscape

android.manifest.intent_filters = <intent-filter><action android:name="android.intent.action.MAIN"/><category android:name="android.intent.category.LAUNCHER"/><category android:name="android.intent.category.LEANBACK_LAUNCHER"/></intent-filter>
android.meta_data = uses-feature:android.software.leanback:false,uses-feature:android.hardware.touchscreen:false

[buildozer]
log_level = 2
warn_on_root = 1

# Ландшафт по умолчанию — удобно для TV, в телефоне всё равно адаптив
android.orientation = landscape

# TV-совместимость: LEANBACK_LAUNCHER категория
android.manifest.intent_filters = <intent-filter><action android:name="android.intent.action.MAIN"/><category android:name="android.intent.category.LAUNCHER"/><category android:name="android.intent.category.LEANBACK_LAUNCHER"/></intent-filter>

# Требуем отсутствия тачскрина — Google Play покажет в разделе TV
android.meta_data = uses-feature:android.software.leanback:false,uses-feature:android.hardware.touchscreen:false

[buildozer]
log_level = 2
warn_on_root = 1