# Сторонние компоненты

Этот пакет содержит исходники рабочего процесса, без чужих фотографий, клонов голоса или готовых персонажей. Новые материалы создаются из ресурсов получателя.

- Remotion 4.0.526 и React: версии закреплены в package.json. Условия Remotion: https://www.remotion.dev/license ; React: https://github.com/facebook/react/blob/main/LICENSE . Наличие кода не означает оплаченной коммерческой лицензии.
- FFmpeg устанавливается отдельно; лицензия зависит от сборки: https://ffmpeg.org/legal.html .
- faster-whisper / Whisper: https://github.com/SYSTRAN/faster-whisper , https://huggingface.co/Systran/faster-whisper-small (по умолчанию) и https://huggingface.co/Systran/faster-whisper-medium (по желанию).
- Только платная версия, второй язык: Kokoro ONNX (https://github.com/thewh1teagle/kokoro-onnx , https://huggingface.co/hexgrad/Kokoro-82M ), espeak-ng (https://github.com/espeak-ng/espeak-ng ) и ONNX wav2vec2 (https://huggingface.co/onnx-community/wav2vec2-base-960h-ONNX ). В бесплатной версии они не ставятся: зависимости лежат в `requirements-pro.txt`. У каждого компонента собственная лицензия, включая требования GPL у espeak-ng; веса загружаются из первоисточников и в архив не входят.
- Montserrat ExtraBold (шрифт субтитров по умолчанию, `workspace/fonts/Montserrat-ExtraBold.woff2`): SIL Open Font License 1.1, https://github.com/JulietaUla/Montserrat . Полный текст лицензии: `workspace/fonts/OFL.txt`. Файл переведён из TTF в WOFF2 без изменения глифов.
- Nunito: SIL Open Font License, https://github.com/google/fonts/tree/main/ofl/nunito . Шрифт используется в английском readalong; полный OFL приложен, если шрифт входит в пакет.
- ElevenLabs (по желанию) и HeyGen (только платная версия) подключаются только к собственным аккаунтам получателя и по их условиям. Ключи, подписки, голосовые клоны и аватары в комплект не входят. Работоспособность конкретного тарифа/голоса/модели проверяется при настройке; платная попытка требует выбранного бюджета.

Включённые исходники предоставляются по LICENSE (MIT). Исходная строка копирайта сохранена, строка об изменениях добавлена отдельно. Лицензия не отменяет условий сторонних компонентов.
