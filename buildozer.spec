[app]
title = Elena Music
package.name = elena
package.domain = org.elena

source.dir = .
source.include_exts = py,png,jpg,kv,atlas,json,html,css,js,ttf,pem,bin,txt,ogg,wav,mdl
source.exclude_dirs = tests,bin,.buildozer,__pycache__,certs,cache,.git,models,vosk-model-ru-0.42,vosk-small-ru
source.exclude_patterns = *.pyc,*.pyo,*.so,*.apk,*.aab

version = 1.0.0

requirements = python3,kivy==2.2.1,fastapi,uvicorn,pydantic,starlette,requests,urllib3,certifi,idna,charset-normalizer,yandex-music,soundfile,numpy,cryptography,pyjnius,pillow,android

android.permissions = INTERNET,RECORD_AUDIO,WAKE_LOCK,FOREGROUND_SERVICE,ACCESS_NETWORK_STATE,ACCESS_WIFI_STATE,CHANGE_WIFI_MULTICAST_STATE
android.api = 33
android.minapi = 24
android.ndk = 25b
android.archs = arm64-v8a, armeabi-v7a
android.accept_sdk_license = True
android.allow_backup = True
android.orientation = landscape

android.manifest.intent_filters = <intent-filter><action android:name="android.intent.action.MAIN"/><category android:name="android.intent.category.LAUNCHER"/><category android:name="android.intent.category.LEANBACK_LAUNCHER"/></intent-filter>
android.meta_data = uses-feature:android.software.leanback:false,uses-feature:android.hardware.touchscreen:false

p4a.branch = v2024.01.21

[buildozer]
log_level = 2
warn_on_root = 1
