"""All user-facing strings, in eight languages.

Every string that a handler can show lives here as a key with a value for each
language in :data:`LANGS`. The :func:`t` helper selects the user's language and applies
``str.format`` kwargs. A completeness test (``tests/test_i18n.py``) enforces that every
key is present in *every* language and that there are no orphan keys, so nothing can
ship half-translated.

Nothing in the handlers hardcodes user-facing text — they all call :func:`t`.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from pathlib import Path

from .db import connect

LANGS: tuple[str, ...] = ("en", "ar", "tr", "es", "ru", "fr", "de", "pt")
FALLBACK_LANG = "en"

# key -> {en: ..., ar: ..., tr: ..., es: ..., ru: ..., fr: ..., de: ..., pt: ...}
# All values are final user-facing copy. Keep tags/labels lowercase where they are
# meant to be filtered (see plex.py).
STRINGS: dict[str, dict[str, str]] = {
    # --- Menu ---
    "menu_title": {
        "en": "📺 Video Archive",
        "ar": "📺 أرشيف الفيديو",
        "tr": "📺 Video Arşivi",
        "es": "📺 Archivo de video",
        "ru": "📺 Видеоархив",
        "fr": "📺 Archive vidéo",
        "de": "📺 Videoarchiv",
        "pt": "📺 Arquivo de vídeo",
    },
    "menu_add": {
        "en": "➕ Add a video",
        "ar": "➕ إضافة فيديو",
        "tr": "➕ Video ekle",
        "es": "➕ Añadir un video",
        "ru": "➕ Добавить видео",
        "fr": "➕ Ajouter une vidéo",
        "de": "➕ Video hinzufügen",
        "pt": "➕ Adicionar um vídeo",
    },
    "menu_queue": {
        "en": "🕒 Queue",
        "ar": "🕒 قائمة الانتظار",
        "tr": "🕒 Sıra",
        "es": "🕒 Cola",
        "ru": "🕒 Очередь",
        "fr": "🕒 File d'attente",
        "de": "🕒 Warteschlange",
        "pt": "🕒 Fila",
    },
    "menu_library": {
        "en": "📚 Library",
        "ar": "📚 المكتبة",
        "tr": "📚 Kütüphane",
        "es": "📚 Biblioteca",
        "ru": "📚 Библиотека",
        "fr": "📚 Bibliothèque",
        "de": "📚 Bibliothek",
        "pt": "📚 Biblioteca",
    },
    "menu_stats": {
        "en": "📊 Stats",
        "ar": "📊 الإحصائيات",
        "tr": "📊 İstatistikler",
        "es": "📊 Estadísticas",
        "ru": "📊 Статистика",
        "fr": "📊 Statistiques",
        "de": "📊 Statistiken",
        "pt": "📊 Estatísticas",
    },
    "menu_lang": {
        "en": "🌐 Language",
        "ar": "🌐 اللغة",
        "tr": "🌐 Dil",
        "es": "🌐 Idioma",
        "ru": "🌐 Язык",
        "fr": "🌐 Langue",
        "de": "🌐 Sprache",
        "pt": "🌐 Idioma",
    },
    # Shown when "go to menu" is tapped/sent while the menu is already open in
    # this language: the canonical message already shows it, so we confirm rather
    # than resend.
    "menu_here": {
        "en": "Menu is already open (pinned above your keyboard)",
        "ar": "القائمة مفتوحة بالفعل",
        "tr": "Menü zaten açık (klavyenin üstünde sabitlenmiş)",
        "es": "El menú ya está abierto (fijado sobre tu teclado)",
        "ru": "Меню уже открыто (закреплено над клавиатурой)",
        "fr": "Le menu est déjà ouvert (épinglé au-dessus du clavier)",
        "de": "Menü ist bereits geöffnet (über der Tastatur angeheftet)",
        "pt": "O menu já está aberto (fixado acima do teclado)",
    },
    # --- Add flow ---
    "add_prompt": {
        "en": "Paste a video, playlist, or channel URL (any site yt-dlp supports):",
        "ar": "ألصق رابط فيديو أو قائمة تشغيل أو قناة (أي موقع يدعمه yt-dlp):",
        "tr": "Bir video, oynatma listesi veya kanal bağlantısı yapıştırın (yt-dlp'nin desteklediği herhangi bir site):",
        "es": "Pega un enlace de video, lista de reproducción o canal (cualquier sitio compatible con yt-dlp):",
        "ru": "Вставьте ссылку на видео, плейлист или канал (любой сайт, поддерживаемый yt-dlp):",
        "fr": "Collez un lien vers une vidéo, une playlist ou une chaîne (tout site pris en charge par yt-dlp) :",
        "de": "Füge einen Link zu einem Video, einer Playlist oder einem Kanal ein (jede von yt-dlp unterstützte Seite):",
        "pt": "Cole um link de vídeo, playlist ou canal (qualquer site suportado pelo yt-dlp):",
    },
    "probing": {
        "en": "🔎 Fetching video info…",
        "ar": "🔎 جاري جلب معلومات الفيديو…",
        "tr": "🔎 Video bilgileri getiriliyor…",
        "es": "🔎 Obteniendo información del video…",
        "ru": "🔎 Получение информации о видео…",
        "fr": "🔎 Récupération des infos de la vidéo…",
        "de": "🔎 Videoinformationen werden abgerufen…",
        "pt": "🔎 Buscando informações do vídeo…",
    },
    "not_a_url": {
        "en": "That doesn't look like a URL. Paste a link to a video, playlist, or channel.",
        "ar": "هذا لا يبدو رابطاً. ألصق رابط فيديو أو قائمة تشغيل أو قناة.",
        "tr": "Bu bir bağlantı gibi görünmüyor. Video, oynatma listesi veya kanal bağlantısı yapıştırın.",
        "es": "Eso no parece un enlace. Pega un enlace a un video, lista de reproducción o canal.",
        "ru": "Это не похоже на ссылку. Вставьте ссылку на видео, плейлист или канал.",
        "fr": "Cela ne ressemble pas à un lien. Collez un lien vers une vidéo, une playlist ou une chaîne.",
        "de": "Das sieht nicht nach einem Link aus. Füge einen Link zu Video, Playlist oder Kanal ein.",
        "pt": "Isso não parece um link. Cole um link de vídeo, playlist ou canal.",
    },
    "detail_title": {
        "en": "Title",
        "ar": "العنوان",
        "tr": "Başlık",
        "es": "Título",
        "ru": "Название",
        "fr": "Titre",
        "de": "Titel",
        "pt": "Título",
    },
    "detail_site": {
        "en": "Site",
        "ar": "الموقع",
        "tr": "Site",
        "es": "Sitio",
        "ru": "Сайт",
        "fr": "Site",
        "de": "Seite",
        "pt": "Site",
    },
    "detail_channel": {
        "en": "Channel",
        "ar": "القناة",
        "tr": "Kanal",
        "es": "Canal",
        "ru": "Канал",
        "fr": "Chaîne",
        "de": "Kanal",
        "pt": "Canal",
    },
    "detail_duration": {
        "en": "Duration",
        "ar": "المدة",
        "tr": "Süre",
        "es": "Duración",
        "ru": "Длительность",
        "fr": "Durée",
        "de": "Dauer",
        "pt": "Duração",
    },
    "detail_resolution": {
        "en": "Quality",
        "ar": "الجودة",
        "tr": "Kalite",
        "es": "Calidad",
        "ru": "Качество",
        "fr": "Qualité",
        "de": "Qualität",
        "pt": "Qualidade",
    },
    "detail_audio": {
        "en": "Audio tracks",
        "ar": "مسارات الصوت",
        "tr": "Ses parçaları",
        "es": "Pistas de audio",
        "ru": "Аудиодорожки",
        "fr": "Pistes audio",
        "de": "Audiospuren",
        "pt": "Faixas de áudio",
    },
    "audio_none": {
        "en": "(single default track)",
        "ar": "(مسار افتراضي واحد)",
        "tr": "(tek varsayılan parça)",
        "es": "(una sola pista predeterminada)",
        "ru": "(одна дорожка по умолчанию)",
        "fr": "(une seule piste par défaut)",
        "de": "(eine Standardspur)",
        "pt": "(uma faixa padrão única)",
    },
    "confirm_hint": {
        "en": "Select a quality, then tap a ⬇️ Download button to start.",
        "ar": "اختر الجودة ثم اضغط زر ⬇️ التحميل للبدء.",
        "tr": "Bir kalite seçin, ardından başlatmak için ⬇️ İndir düğmesine dokunun.",
        "es": "Elige una calidad y luego toca un botón ⬇️ Descargar para empezar.",
        "ru": "Выберите качество, затем нажмите кнопку ⬇️ Скачать.",
        "fr": "Choisissez une qualité, puis appuyez sur un bouton ⬇️ Télécharger.",
        "de": "Wähle eine Qualität und tippe dann auf eine ⬇️ Herunterladen-Schaltfläche.",
        "pt": "Escolha uma qualidade e toque em um botão ⬇️ Baixar.",
    },
    "video_already_downloaded": {
        "en": "✅ Video already in your library — you can still download the audio-only version.",
        "ar": "✅ الفيديو موجود بالفعل في مكتبتك — يمكنك تنزيل النسخة الصوتية فقط.",
        "tr": "✅ Video zaten kütüphanenizde — yine de yalnızca ses sürümünü indirebilirsiniz.",
        "es": "✅ El video ya está en tu biblioteca — aún puedes descargar la versión solo audio.",
        "ru": "✅ Видео уже в вашей библиотеке — вы всё ещё можете скачать версию только со звуком.",
        "fr": "✅ La vidéo est déjà dans votre bibliothèque — vous pouvez encore télécharger la version audio seule.",
        "de": "✅ Video bereits in deiner Bibliothek — du kannst weiterhin die Nur-Audio-Version herunterladen.",
        "pt": "✅ O vídeo já está na sua biblioteca — você ainda pode baixar a versão somente áudio.",
    },
    "btn_download_all": {
        "en": "⬇️ Download (all audio)",
        "ar": "⬇️ تحميل (كل المسارات)",
        "tr": "⬇️ İndir (tüm sesler)",
        "es": "⬇️ Descargar (todo el audio)",
        "ru": "⬇️ Скачать (весь звук)",
        "fr": "⬇️ Télécharger (tout l'audio)",
        "de": "⬇️ Herunterladen (gesamtes Audio)",
        "pt": "⬇️ Baixar (todo o áudio)",
    },
    "btn_original_audio": {
        "en": "⬇️ Download (original audio)",
        "ar": "⬇️ تحميل (الصوت الأصلي)",
        "tr": "⬇️ İndir (orijinal ses)",
        "es": "⬇️ Descargar (audio original)",
        "ru": "⬇️ Скачать (исходный звук)",
        "fr": "⬇️ Télécharger (audio d'origine)",
        "de": "⬇️ Herunterladen (Originalaudio)",
        "pt": "⬇️ Baixar (áudio original)",
    },
    "btn_audio_only": {
        "en": "🎵 Audio only (no video)",
        "ar": "🎵 صوت فقط (بدون فيديو)",
        "tr": "🎵 Yalnızca ses (video yok)",
        "es": "🎵 Solo audio (sin video)",
        "ru": "🎵 Только аудио (без видео)",
        "fr": "🎵 Audio uniquement (sans vidéo)",
        "de": "🎵 Nur Audio (ohne Video)",
        "pt": "🎵 Somente áudio (sem vídeo)",
    },
    "btn_audio_only_n": {
        "en": "🎵 Audio only (all {n})",
        "ar": "🎵 صوت فقط (الكل {n})",
        "tr": "🎵 Yalnızca ses (tümü {n})",
        "es": "🎵 Solo audio (todo {n})",
        "ru": "🎵 Только аудио (все {n})",
        "fr": "🎵 Audio uniquement (tout {n})",
        "de": "🎵 Nur Audio (alle {n})",
        "pt": "🎵 Somente áudio (todos {n})",
    },
    "btn_cancel": {
        "en": "❌ Cancel",
        "ar": "❌ إلغاء",
        "tr": "❌ İptal",
        "es": "❌ Cancelar",
        "ru": "❌ Отмена",
        "fr": "❌ Annuler",
        "de": "❌ Abbrechen",
        "pt": "❌ Cancelar",
    },
    "btn_download_all_n": {
        "en": "⬇️ Download all {n}",
        "ar": "⬇️ تحميل الكل ({n})",
        "tr": "⬇️ Tümünü indir ({n})",
        "es": "⬇️ Descargar todo {n}",
        "ru": "⬇️ Скачать все {n}",
        "fr": "⬇️ Tout télécharger ({n})",
        "de": "⬇️ Alle {n} herunterladen",
        "pt": "⬇️ Baixar todos {n}",
    },
    "btn_next": {
        "en": "⏭️ Next",
        "ar": "⏭️ التالي",
        "tr": "⏭️ Sonraki",
        "es": "⏭️ Siguiente",
        "ru": "⏭️ Далее",
        "fr": "⏭️ Suivant",
        "de": "⏭️ Weiter",
        "pt": "⏭️ Próximo",
    },
    "btn_back": {
        "en": "⬅️ Back",
        "ar": "⬅️ رجوع",
        "tr": "⬅️ Geri",
        "es": "⬅️ Atrás",
        "ru": "⬅️ Назад",
        "fr": "⬅️ Retour",
        "de": "⬅️ Zurück",
        "pt": "⬅️ Voltar",
    },
    "already_in_library": {
        "en": "✅ Already in your library — skipped.",
        "ar": "✅ موجود مسبقاً في مكتبتك — تم التجاهل.",
        "tr": "✅ Zaten kütüphanenizde — atlandı.",
        "es": "✅ Ya está en tu biblioteca — omitido.",
        "ru": "✅ Уже в вашей библиотеке — пропущено.",
        "fr": "✅ Déjà dans votre bibliothèque — ignoré.",
        "de": "✅ Bereits in deiner Bibliothek — übersprungen.",
        "pt": "✅ Já está na sua biblioteca — ignorado.",
    },
    "added_one": {
        "en": "📥 Added to queue (position {pos}).",
        "ar": "📥 أُضيف إلى قائمة الانتظار (الموضع {pos}).",
        "tr": "📥 Sıraya eklendi (sıra {pos}).",
        "es": "📥 Añadido a la cola (posición {pos}).",
        "ru": "📥 Добавлено в очередь (позиция {pos}).",
        "fr": "📥 Ajouté à la file (position {pos}).",
        "de": "📥 Zur Warteschlange hinzugefügt (Position {pos}).",
        "pt": "📥 Adicionado à fila (posição {pos}).",
    },
    "added_many": {
        "en": "📥 Added {n} videos to the queue.",
        "ar": "📥 أُضيف {n} فيديو إلى قائمة الانتظار.",
        "tr": "📥 Sıraya {n} video eklendi.",
        "es": "📥 {n} videos añadidos a la cola.",
        "ru": "📥 В очередь добавлено видео: {n}.",
        "fr": "📥 {n} vidéos ajoutées à la file.",
        "de": "📥 {n} Videos zur Warteschlange hinzugefügt.",
        "pt": "📥 {n} vídeos adicionados à fila.",
    },
    "added_many_skip": {
        "en": "📥 Added {n} to the queue; {m} already in library.",
        "ar": "📥 أُضيف {n} إلى قائمة الانتظار؛ {m} موجود مسبقاً في المكتبة.",
        "tr": "📥 {n} sıraya eklendi; {m} zaten kütüphanede.",
        "es": "📥 {n} añadidos a la cola; {m} ya en la biblioteca.",
        "ru": "📥 Добавлено в очередь: {n}; в библиотеке уже: {m}.",
        "fr": "📥 {n} ajoutés à la file ; {m} déjà dans la bibliothèque.",
        "de": "📥 {n} zur Warteschlange hinzugefügt; {m} bereits in der Bibliothek.",
        "pt": "📥 {n} adicionados à fila; {m} já na biblioteca.",
    },
    "playlist_warn": {
        "en": "⚠️ This playlist/channel has {n} entries. “Download all” will queue every one of them.",
        "ar": "⚠️ هذه القائمة/القناة تحتوي على {n} عنصر. «تحميل الكل» سيضيف جميعها إلى قائمة الانتظار.",
        "tr": "⚠️ Bu oynatma listesi/kanal {n} öğe içeriyor. «Tümünü indir» hepsini sıraya ekleyecek.",
        "es": "⚠️ Esta lista/canal tiene {n} elementos. «Descargar todo» pondrá todos en la cola.",
        "ru": "⚠️ В этом плейлисте/канале {n} элементов. «Скачать все» добавит в очередь каждый из них.",
        "fr": "⚠️ Cette playlist/chaîne contient {n} éléments. « Tout télécharger » mettra chacun en file.",
        "de": "⚠️ Diese Playlist/dieser Kanal hat {n} Einträge. „Alle herunterladen“ reiht jeden davon ein.",
        "pt": "⚠️ Esta playlist/canal tem {n} itens. «Baixar todos» colocará cada um na fila.",
    },
    # --- Video file (uploaded to the bot) ---
    "video_receiving": {
        "en": "📥 Receiving your video…",
        "ar": "📥 جاري استلام الفيديو…",
        "tr": "📥 Videonuz alınıyor…",
        "es": "📥 Recibiendo tu video…",
        "ru": "📥 Получение вашего видео…",
        "fr": "📥 Réception de votre vidéo…",
        "de": "📥 Video wird empfangen…",
        "pt": "📥 Recebendo seu vídeo…",
    },
    "video_error": {
        "en": "😕 Couldn't receive that video file. Please try again.",
        "ar": "😕 تعذّر استلام ملف الفيديو. حاول مرة أخرى.",
        "tr": "😕 Bu video dosyası alınamadı. Lütfen tekrar deneyin.",
        "es": "😕 No se pudo recibir ese archivo de video. Inténtalo de nuevo.",
        "ru": "😕 Не удалось получить видеофайл. Попробуйте ещё раз.",
        "fr": "😕 Impossible de recevoir ce fichier vidéo. Veuillez réessayer.",
        "de": "😕 Diese Videodatei konnte nicht empfangen werden. Bitte versuche es erneut.",
        "pt": "😕 Não foi possível receber esse arquivo de vídeo. Tente novamente.",
    },
    "video_too_big": {
        "en": "😕 This video is over 20 MB, which the standard Bot API can't receive. Please send me a link to the video instead.",
        "ar": "😕 هذا الفيديو أكبر من 20 ميغابايت ولا يمكن استقباله عبر واجهة البوت القياسية. أرسل لي رابط الفيديو بدلاً من ذلك.",
        "tr": "😕 Bu video 20 MB'ın üzerinde ve standart Bot API bunu alamıyor. Bunun yerine bana videonun bağlantısını gönderin.",
        "es": "😕 Este video supera los 20 MB, que la API de bots estándar no puede recibir. Envíame un enlace al video.",
        "ru": "😕 Это видео больше 20 МБ — стандартный Bot API не может его принять. Пришлите вместо этого ссылку на видео.",
        "fr": "😕 Cette vidéo dépasse 20 Mo, ce que l'API de bot standard ne peut pas recevoir. Envoyez-moi plutôt un lien vers la vidéo.",
        "de": "😕 Dieses Video ist über 20 MB groß — das kann die Standard-Bot-API nicht empfangen. Schicke mir stattdessen einen Link zum Video.",
        "pt": "😕 Este vídeo tem mais de 20 MB, que a API de bots padrão não consegue receber. Envie-me um link para o vídeo.",
    },
    "video_default_title": {
        "en": "Uploaded Video",
        "ar": "فيديو مرفوع",
        "tr": "Yüklenen Video",
        "es": "Video subido",
        "ru": "Загруженное видео",
        "fr": "Vidéo téléversée",
        "de": "Hochgeladenes Video",
        "pt": "Vídeo enviado",
    },
    # --- Download lifecycle ---
    "queued": {
        "en": "⏳ In queue (position {pos})…",
        "ar": "⏳ في قائمة الانتظار (الموضع {pos})…",
        "tr": "⏳ Sırada (pozisyon {pos})…",
        "es": "⏳ En cola (posición {pos})…",
        "ru": "⏳ В очереди (позиция {pos})…",
        "fr": "⏳ En file (position {pos})…",
        "de": "⏳ In Warteschlange (Position {pos})…",
        "pt": "⏳ Na fila (posição {pos})…",
    },
    "downloading": {
        "en": "⬇️ Downloading {title}",
        "ar": "⬇️ جارٍ تنزيل {title}",
        "tr": "⬇️ İndiriliyor: {title}",
        "es": "⬇️ Descargando {title}",
        "ru": "⬇️ Загрузка: {title}",
        "fr": "⬇️ Téléchargement de {title}",
        "de": "⬇️ Lade {title} herunter",
        "pt": "⬇️ Baixando {title}",
    },
    "progress": {
        "en": "📶 {pct}%  ·  {speed}  ·  ETA {eta}",
        "ar": "📶 {pct}%  ·  {speed}  ·  المتبقي {eta}",
        "tr": "📶 {pct}%  ·  {speed}  ·  Kalan {eta}",
        "es": "📶 {pct}%  ·  {speed}  ·  ETA {eta}",
        "ru": "📶 {pct}%  ·  {speed}  ·  Осталось {eta}",
        "fr": "📶 {pct}%  ·  {speed}  ·  ETA {eta}",
        "de": "📶 {pct}%  ·  {speed}  ·  ETA {eta}",
        "pt": "📶 {pct}%  ·  {speed}  ·  ETA {eta}",
    },
    "done": {
        "en": "✅ Done\n📁 {path}\n💾 {size}\n🎵 {tracks} audio track(s)",
        "ar": "✅ اكتمل\n📁 {path}\n💾 {size}\n🎵 عدد مسارات الصوت: {tracks}",
        "tr": "✅ Tamamlandı\n📁 {path}\n💾 {size}\n🎵 {tracks} ses parçası",
        "es": "✅ Listo\n📁 {path}\n💾 {size}\n🎵 {tracks} pista(s) de audio",
        "ru": "✅ Готово\n📁 {path}\n💾 {size}\n🎵 Аудиодорожек: {tracks}",
        "fr": "✅ Terminé\n📁 {path}\n💾 {size}\n🎵 {tracks} piste(s) audio",
        "de": "✅ Fertig\n📁 {path}\n💾 {size}\n🎵 {tracks} Audiospur(en)",
        "pt": "✅ Concluído\n📁 {path}\n💾 {size}\n🎵 {tracks} faixa(s) de áudio",
    },
    "failed": {
        "en": "⚠️ Failed: {reason}",
        "ar": "⚠️ فشل: {reason}",
        "tr": "⚠️ Başarısız: {reason}",
        "es": "⚠️ Falló: {reason}",
        "ru": "⚠️ Ошибка: {reason}",
        "fr": "⚠️ Échec : {reason}",
        "de": "⚠️ Fehlgeschlagen: {reason}",
        "pt": "⚠️ Falhou: {reason}",
    },
    "cancelled": {
        "en": "🚫 Cancelled — partial files removed.",
        "ar": "🚫 أُلغي — حُذفت الملفات الجزئية.",
        "tr": "🚫 İptal edildi — kısmi dosyalar kaldırıldı.",
        "es": "🚫 Cancelado — se eliminaron los archivos parciales.",
        "ru": "🚫 Отменено — частичные файлы удалены.",
        "fr": "🚫 Annulé — fichiers partiels supprimés.",
        "de": "🚫 Abgebrochen — Teil-Dateien entfernt.",
        "pt": "🚫 Cancelado — arquivos parciais removidos.",
    },
    # --- Queue view ---
    "queue_title": {
        "en": "🕒 Queue",
        "ar": "🕒 قائمة الانتظار",
        "tr": "🕒 Sıra",
        "es": "🕒 Cola",
        "ru": "🕒 Очередь",
        "fr": "🕒 File d'attente",
        "de": "🕒 Warteschlange",
        "pt": "🕒 Fila",
    },
    "no_jobs": {
        "en": "🕒 Queue is empty.",
        "ar": "🕒 قائمة الانتظار فارغة.",
        "tr": "🕒 Sıra boş.",
        "es": "🕒 La cola está vacía.",
        "ru": "🕒 Очередь пуста.",
        "fr": "🕒 La file est vide.",
        "de": "🕒 Warteschlange ist leer.",
        "pt": "🕒 A fila está vazia.",
    },
    "job_running": {
        "en": "⬇️ {title} — {pct}%",
        "ar": "⬇️ {title} — {pct}%",
        "tr": "⬇️ {title} — {pct}%",
        "es": "⬇️ {title} — {pct}%",
        "ru": "⬇️ {title} — {pct}%",
        "fr": "⬇️ {title} — {pct}%",
        "de": "⬇️ {title} — {pct}%",
        "pt": "⬇️ {title} — {pct}%",
    },
    "job_queued": {
        "en": "⏳ {title}",
        "ar": "⏳ {title}",
        "tr": "⏳ {title}",
        "es": "⏳ {title}",
        "ru": "⏳ {title}",
        "fr": "⏳ {title}",
        "de": "⏳ {title}",
        "pt": "⏳ {title}",
    },
    "job_done": {
        "en": "✅ {title}",
        "ar": "✅ {title}",
        "tr": "✅ {title}",
        "es": "✅ {title}",
        "ru": "✅ {title}",
        "fr": "✅ {title}",
        "de": "✅ {title}",
        "pt": "✅ {title}",
    },
    "job_failed": {
        "en": "⚠️ {title} — {reason}",
        "ar": "⚠️ {title} — {reason}",
        "tr": "⚠️ {title} — {reason}",
        "es": "⚠️ {title} — {reason}",
        "ru": "⚠️ {title} — {reason}",
        "fr": "⚠️ {title} — {reason}",
        "de": "⚠️ {title} — {reason}",
        "pt": "⚠️ {title} — {reason}",
    },
    "job_cancel": {
        "en": "🚫 Cancel",
        "ar": "🚫 إلغاء",
        "tr": "🚫 İptal",
        "es": "🚫 Cancelar",
        "ru": "🚫 Отмена",
        "fr": "🚫 Annuler",
        "de": "🚫 Abbrechen",
        "pt": "🚫 Cancelar",
    },
    "queue_menu": {
        "en": "🏠 Menu",
        "ar": "🏠 القائمة",
        "tr": "🏠 Menü",
        "es": "🏠 Menú",
        "ru": "🏠 Меню",
        "fr": "🏠 Menu",
        "de": "🏠 Menü",
        "pt": "🏠 Menu",
    },
    # --- Library ---
    "library_title": {
        "en": "📚 Library",
        "ar": "📚 المكتبة",
        "tr": "📚 Kütüphane",
        "es": "📚 Biblioteca",
        "ru": "📚 Библиотека",
        "fr": "📚 Bibliothèque",
        "de": "📚 Bibliothek",
        "pt": "📚 Biblioteca",
    },
    "library_empty": {
        "en": "Your library is empty. Add a video to get started!",
        "ar": "مكتبتك فارغة. أضف فيديو للبدء!",
        "tr": "Kütüphaneniz boş. Başlamak için bir video ekleyin!",
        "es": "Tu biblioteca está vacía. ¡Añade un video para empezar!",
        "ru": "Ваша библиотека пуста. Добавьте видео, чтобы начать!",
        "fr": "Votre bibliothèque est vide. Ajoutez une vidéo pour commencer !",
        "de": "Deine Bibliothek ist leer. Füge ein Video hinzu, um zu starten!",
        "pt": "Sua biblioteca está vazia. Adicione um vídeo para começar!",
    },
    "library_site": {
        "en": "📺 {site} ({n})",
        "ar": "📺 {site} ({n})",
        "tr": "📺 {site} ({n})",
        "es": "📺 {site} ({n})",
        "ru": "📺 {site} ({n})",
        "fr": "📺 {site} ({n})",
        "de": "📺 {site} ({n})",
        "pt": "📺 {site} ({n})",
    },
    "library_channel": {
        "en": "📁 {channel} ({n})",
        "ar": "📁 {channel} ({n})",
        "tr": "📁 {channel} ({n})",
        "es": "📁 {channel} ({n})",
        "ru": "📁 {channel} ({n})",
        "fr": "📁 {channel} ({n})",
        "de": "📁 {channel} ({n})",
        "pt": "📁 {channel} ({n})",
    },
    "library_file": {
        "en": "• {name}\n  {size}  ·  {date}  ·  🎵 {audio}",
        "ar": "• {name}\n  {size}  ·  {date}  ·  🎵 {audio}",
        "tr": "• {name}\n  {size}  ·  {date}  ·  🎵 {audio}",
        "es": "• {name}\n  {size}  ·  {date}  ·  🎵 {audio}",
        "ru": "• {name}\n  {size}  ·  {date}  ·  🎵 {audio}",
        "fr": "• {name}\n  {size}  ·  {date}  ·  🎵 {audio}",
        "de": "• {name}\n  {size}  ·  {date}  ·  🎵 {audio}",
        "pt": "• {name}\n  {size}  ·  {date}  ·  🎵 {audio}",
    },
    "library_back": {
        "en": "⬆️ Up",
        "ar": "⬆️ أعلى",
        "tr": "⬆️ Yukarı",
        "es": "⬆️ Arriba",
        "ru": "⬆️ Вверх",
        "fr": "⬆️ Haut",
        "de": "⬆️ Hoch",
        "pt": "⬆️ Acima",
    },
    "library_file_audio_single": {
        "en": "1 track",
        "ar": "مسار واحد",
        "tr": "1 parça",
        "es": "1 pista",
        "ru": "1 дорожка",
        "fr": "1 piste",
        "de": "1 Spur",
        "pt": "1 faixa",
    },
    "library_file_audio_none": {
        "en": "no track info",
        "ar": "لا توجد معلومات",
        "tr": "parça bilgisi yok",
        "es": "sin info de pista",
        "ru": "нет данных о дорожке",
        "fr": "aucune info de piste",
        "de": "keine Spurinfo",
        "pt": "sem info de faixa",
    },
    "delete_confirm": {
        "en": "Delete this item from your library?\n{title}",
        "ar": "حذف هذا العنصر من مكتبتك؟\n{title}",
        "tr": "Bu öğe kütüphanenizden silinsin mi?\n{title}",
        "es": "¿Eliminar este elemento de tu biblioteca?\n{title}",
        "ru": "Удалить этот элемент из библиотеки?\n{title}",
        "fr": "Supprimer cet élément de votre bibliothèque ?\n{title}",
        "de": "Dieses Element aus deiner Bibliothek löschen?\n{title}",
        "pt": "Excluir este item da sua biblioteca?\n{title}",
    },
    "delete_yes": {
        "en": "🗑 Yes, delete",
        "ar": "🗑 نعم، احذف",
        "tr": "🗑 Evet, sil",
        "es": "🗑 Sí, eliminar",
        "ru": "🗑 Да, удалить",
        "fr": "🗑 Oui, supprimer",
        "de": "🗑 Ja, löschen",
        "pt": "🗑 Sim, excluir",
    },
    "delete_no": {
        "en": "⬆️ Cancel",
        "ar": "⬆️ إلغاء",
        "tr": "⬆️ İptal",
        "es": "⬆️ Cancelar",
        "ru": "⬆️ Отмена",
        "fr": "⬆️ Annuler",
        "de": "⬆️ Abbrechen",
        "pt": "⬆️ Cancelar",
    },
    "deleted_ok": {
        "en": "✅ Deleted. It's also off the dedupe ledger — send the link again any time to re-download it.",
        "ar": "✅ تم الحذف وإزالته من سجل التكرار — أرسل الرابط مجدداً في أي وقت لإعادة التحميل.",
        "tr": "✅ Silindi. Ayrıca tekrar-kayıt defterinden de kaldırıldı — yeniden indirmek için bağlantıyı istediğiniz zaman tekrar gönderin.",
        "es": "✅ Eliminado. También se quitó del registro de deduplicación — reenvía el enlace cuando quieras para volver a descargarlo.",
        "ru": "✅ Удалено. Также убрано из журнала дедупликации — отправьте ссылку снова в любой момент, чтобы скачать заново.",
        "fr": "✅ Supprimé. Retiré aussi du registre de déduplication — renvoyez le lien à tout moment pour le re-télécharger.",
        "de": "✅ Gelöscht. Auch aus dem Deduplizierungs-Ledger entfernt — sende den Link jederzeit erneut, um ihn erneut herunterzuladen.",
        "pt": "✅ Excluído. Também foi removido do registro de deduplicação — reenvie o link a qualquer momento para baixá-lo novamente.",
    },
    "delete_failed": {
        "en": "⚠️ Deletion failed — nothing was removed. Check the bot logs.",
        "ar": "⚠️ فشل الحذف — لم يُحذف أي شيء. راجع سجلات البوت.",
        "tr": "⚠️ Silme başarısız oldu — hiçbir şey kaldırılmadı. Bot günlüklerini kontrol edin.",
        "es": "⚠️ Falló la eliminación — no se eliminó nada. Revisa los registros del bot.",
        "ru": "⚠️ Не удалось удалить — ничего не удалено. Проверьте журналы бота.",
        "fr": "⚠️ Échec de la suppression — rien n'a été supprimé. Consultez les journaux du bot.",
        "de": "⚠️ Löschen fehlgeschlagen — nichts wurde entfernt. Prüfe die Bot-Protokolle.",
        "pt": "⚠️ Falha ao excluir — nada foi removido. Verifique os logs do bot.",
    },
    "delete_nothing": {
        "en": "Nothing to delete right now — open the Library and pick an item.",
        "ar": "لا يوجد ما يحذف الآن — افتح المكتبة واختر عنصراً.",
        "tr": "Şu anda silinecek bir şey yok — Kütüphane'yi açıp bir öğe seçin.",
        "es": "No hay nada que eliminar ahora — abre la Biblioteca y elige un elemento.",
        "ru": "Сейчас удалять нечего — откройте Библиотеку и выберите элемент.",
        "fr": "Rien à supprimer pour le moment — ouvrez la Bibliothèque et choisissez un élément.",
        "de": "Derzeit nichts zu löschen — öffne die Bibliothek und wähle ein Element.",
        "pt": "Nada para excluir agora — abra a Biblioteca e escolha um item.",
    },
    # --- Stats ---
    "stats_title": {
        "en": "📊 Stats",
        "ar": "📊 الإحصائيات",
        "tr": "📊 İstatistikler",
        "es": "📊 Estadísticas",
        "ru": "📊 Статистика",
        "fr": "📊 Statistiques",
        "de": "📊 Statistiken",
        "pt": "📊 Estatísticas",
    },
    "stats_total": {
        "en": "Total downloads: {n}",
        "ar": "إجمالي التنزيلات: {n}",
        "tr": "Toplam indirme: {n}",
        "es": "Descargas totales: {n}",
        "ru": "Всего загрузок: {n}",
        "fr": "Téléchargements totaux : {n}",
        "de": "Downloads insgesamt: {n}",
        "pt": "Total de downloads: {n}",
    },
    "stats_size": {
        "en": "Total size: {size}",
        "ar": "الحجم الإجمالي: {size}",
        "tr": "Toplam boyut: {size}",
        "es": "Tamaño total: {size}",
        "ru": "Общий размер: {size}",
        "fr": "Taille totale : {size}",
        "de": "Gesamtgröße: {size}",
        "pt": "Tamanho total: {size}",
    },
    "stats_sites": {
        "en": "By site:",
        "ar": "حسب الموقع:",
        "tr": "Siteye göre:",
        "es": "Por sitio:",
        "ru": "По сайтам:",
        "fr": "Par site :",
        "de": "Nach Seite:",
        "pt": "Por site:",
    },
    "stats_site": {
        "en": "  • {site}: {n}",
        "ar": "  • {site}: {n}",
        "tr": "  • {site}: {n}",
        "es": "  • {site}: {n}",
        "ru": "  • {site}: {n}",
        "fr": "  • {site}: {n}",
        "de": "  • {site}: {n}",
        "pt": "  • {site}: {n}",
    },
    "stats_requestors": {
        "en": "By requestor:",
        "ar": "حسب المستخدم:",
        "tr": "Talep edene göre:",
        "es": "Por solicitante:",
        "ru": "По запрашивающим:",
        "fr": "Par demandeur :",
        "de": "Nach Anforderer:",
        "pt": "Por solicitante:",
    },
    "stats_requestor": {
        "en": "  • {who}: {n}",
        "ar": "  • {who}: {n}",
        "tr": "  • {who}: {n}",
        "es": "  • {who}: {n}",
        "ru": "  • {who}: {n}",
        "fr": "  • {who}: {n}",
        "de": "  • {who}: {n}",
        "pt": "  • {who}: {n}",
    },
    "stats_queue": {
        "en": "Queue: {n} job(s)",
        "ar": "قائمة الانتظار: {n} مهمة(ات)",
        "tr": "Sıra: {n} iş",
        "es": "Cola: {n} trabajo(s)",
        "ru": "Очередь: {n} задач(и)",
        "fr": "File : {n} tâche(s)",
        "de": "Warteschlange: {n} Aufgabe(n)",
        "pt": "Fila: {n} trabalho(s)",
    },
    "stats_version": {
        "en": "yt-dlp: {v}",
        "ar": "yt-dlp: {v}",
        "tr": "yt-dlp: {v}",
        "es": "yt-dlp: {v}",
        "ru": "yt-dlp: {v}",
        "fr": "yt-dlp: {v}",
        "de": "yt-dlp: {v}",
        "pt": "yt-dlp: {v}",
    },
    "stats_updated": {
        "en": "Last update check: {t}",
        "ar": "آخر فحص للتحديث: {t}",
        "tr": "Son güncelleme kontrolü: {t}",
        "es": "Última comprobación de actualización: {t}",
        "ru": "Последняя проверка обновлений: {t}",
        "fr": "Dernière vérification de mise à jour : {t}",
        "de": "Letzter Update-Check: {t}",
        "pt": "Última verificação de atualização: {t}",
    },
    "stats_none": {
        "en": "  (none yet)",
        "ar": "  (لا يوجد بعد)",
        "tr": "  (henüz yok)",
        "es": "  (ninguno aún)",
        "ru": "  (пока нет)",
        "fr": "  (aucun pour l'instant)",
        "de": "  (noch keine)",
        "pt": "  (nenhum ainda)",
    },
    # --- Language ---
    "lang_title": {
        "en": "🌐 Choose your language",
        "ar": "🌐 اختر لغتك",
        "tr": "🌐 Dilinizi seçin",
        "es": "🌐 Elige tu idioma",
        "ru": "🌐 Выберите язык",
        "fr": "🌐 Choisissez votre langue",
        "de": "🌐 Wähle deine Sprache",
        "pt": "🌐 Escolha seu idioma",
    },
    "lang_en": {
        "en": "English",
        "ar": "English",
        "tr": "English",
        "es": "English",
        "ru": "English",
        "fr": "English",
        "de": "English",
        "pt": "English",
    },
    "lang_ar": {
        "en": "العربية",
        "ar": "العربية",
        "tr": "العربية",
        "es": "العربية",
        "ru": "العربية",
        "fr": "العربية",
        "de": "العربية",
        "pt": "العربية",
    },
    "lang_tr": {
        "en": "Türkçe",
        "ar": "Türkçe",
        "tr": "Türkçe",
        "es": "Türkçe",
        "ru": "Türkçe",
        "fr": "Türkçe",
        "de": "Türkçe",
        "pt": "Türkçe",
    },
    "lang_es": {
        "en": "Español",
        "ar": "Español",
        "tr": "Español",
        "es": "Español",
        "ru": "Español",
        "fr": "Español",
        "de": "Español",
        "pt": "Español",
    },
    "lang_ru": {
        "en": "Русский",
        "ar": "Русский",
        "tr": "Русский",
        "es": "Русский",
        "ru": "Русский",
        "fr": "Русский",
        "de": "Русский",
        "pt": "Русский",
    },
    "lang_fr": {
        "en": "Français",
        "ar": "Français",
        "tr": "Français",
        "es": "Français",
        "ru": "Français",
        "fr": "Français",
        "de": "Français",
        "pt": "Français",
    },
    "lang_de": {
        "en": "Deutsch",
        "ar": "Deutsch",
        "tr": "Deutsch",
        "es": "Deutsch",
        "ru": "Deutsch",
        "fr": "Deutsch",
        "de": "Deutsch",
        "pt": "Deutsch",
    },
    "lang_pt": {
        "en": "Português",
        "ar": "Português",
        "tr": "Português",
        "es": "Português",
        "ru": "Português",
        "fr": "Português",
        "de": "Português",
        "pt": "Português",
    },
    "lang_set": {
        "en": "✅ Language set to {name}.",
        "ar": "✅ تم ضبط اللغة على {name}.",
        "tr": "✅ Dil {name} olarak ayarlandı.",
        "es": "✅ Idioma cambiado a {name}.",
        "ru": "✅ Язык изменён на {name}.",
        "fr": "✅ Langue définie sur {name}.",
        "de": "✅ Sprache auf {name} gesetzt.",
        "pt": "✅ Idioma definido como {name}.",
    },
    # --- Settings (per-user default download options) ---
    "menu_settings": {
        "en": "⚙️ Settings",
        "ar": "⚙️ الإعدادات",
        "tr": "⚙️ Ayarlar",
        "es": "⚙️ Ajustes",
        "ru": "⚙️ Настройки",
        "fr": "⚙️ Paramètres",
        "de": "⚙️ Einstellungen",
        "pt": "⚙️ Configurações",
    },
    "settings_title": {
        "en": "⚙️ Your download settings",
        "ar": "⚙️ إعدادات التنزيل الخاصة بك",
        "tr": "⚙️ İndirme ayarlarınız",
        "es": "⚙️ Tus ajustes de descarga",
        "ru": "⚙️ Ваши настройки загрузки",
        "fr": "⚙️ Vos paramètres de téléchargement",
        "de": "⚙️ Deine Download-Einstellungen",
        "pt": "⚙️ Suas configurações de download",
    },
    "settings_audio": {
        "en": "Audio: {label}",
        "ar": "الصوت: {label}",
        "tr": "Ses: {label}",
        "es": "Audio: {label}",
        "ru": "Аудио: {label}",
        "fr": "Audio : {label}",
        "de": "Audio: {label}",
        "pt": "Áudio: {label}",
    },
    "settings_quality": {
        "en": "Quality: {label}",
        "ar": "الجودة: {label}",
        "tr": "Kalite: {label}",
        "es": "Calidad: {label}",
        "ru": "Качество: {label}",
        "fr": "Qualité : {label}",
        "de": "Qualität: {label}",
        "pt": "Qualidade: {label}",
    },
    "settings_auto": {
        "en": "Auto-download: {label}",
        "ar": "التنزيل التلقائي: {label}",
        "tr": "Otomatik indirme: {label}",
        "es": "Descarga automática: {label}",
        "ru": "Автозагрузка: {label}",
        "fr": "Téléchargement auto : {label}",
        "de": "Auto-Download: {label}",
        "pt": "Download automático: {label}",
    },
    "settings_hint": {
        "en": "With auto-download on, a link you send is queued right away using these options. Turn it off to be asked every time.",
        "ar": "عند تفعيل التنزيل التلقائي، يتم تحميل الرابط الذي ترسله فوراً بهذه الإعدادات. عطّله لتُسأل في كل مرة.",
        "tr": "Otomatik indirme açıkken gönderdiğiniz bağlantı bu seçeneklerle hemen sıraya eklenir. Her seferinde sorulması için kapatın.",
        "es": "Con la descarga automática activada, un enlace que envíes se pone en cola de inmediato con estas opciones. Desactívala para que se te pregunte cada vez.",
        "ru": "При включённой автозагрузке отправленная ссылка сразу ставится в очередь с этими параметрами. Отключите, чтобы спрашивать каждый раз.",
        "fr": "Avec le téléchargement auto activé, un lien envoyé est mis en file immédiatement avec ces options. Désactivez-le pour être interrogé à chaque fois.",
        "de": "Bei aktiviertem Auto-Download wird ein gesendeter Link sofort mit diesen Optionen eingereiht. Schalte ihn aus, um jedes Mal gefragt zu werden.",
        "pt": "Com o download automático ativado, um link enviado é colocado na fila imediatamente com estas opções. Desative-o para ser perguntado a cada vez.",
    },
    "settings_saved": {
        "en": "✅ Settings saved.",
        "ar": "✅ تم حفظ الإعدادات.",
        "tr": "✅ Ayarlar kaydedildi.",
        "es": "✅ Ajustes guardados.",
        "ru": "✅ Настройки сохранены.",
        "fr": "✅ Paramètres enregistrés.",
        "de": "✅ Einstellungen gespeichert.",
        "pt": "✅ Configurações salvas.",
    },
    "mode_all": {
        "en": "All audio tracks",
        "ar": "كل مسارات الصوت",
        "tr": "Tüm ses parçaları",
        "es": "Todas las pistas de audio",
        "ru": "Все аудиодорожки",
        "fr": "Toutes les pistes audio",
        "de": "Alle Audiospuren",
        "pt": "Todas as faixas de áudio",
    },
    "mode_orig": {
        "en": "Original audio",
        "ar": "الصوت الأصلي",
        "tr": "Orijinal ses",
        "es": "Audio original",
        "ru": "Исходный звук",
        "fr": "Audio d'origine",
        "de": "Originalaudio",
        "pt": "Áudio original",
    },
    "mode_audio": {
        "en": "Audio only (no video)",
        "ar": "صوت فقط (بدون فيديو)",
        "tr": "Yalnızca ses (video yok)",
        "es": "Solo audio (sin video)",
        "ru": "Только аудио (без видео)",
        "fr": "Audio uniquement (sans vidéo)",
        "de": "Nur Audio (ohne Video)",
        "pt": "Somente áudio (sem vídeo)",
    },
    "q_best": {
        "en": "Best",
        "ar": "الأفضل",
        "tr": "En iyi",
        "es": "Mejor",
        "ru": "Лучшее",
        "fr": "Meilleure",
        "de": "Beste",
        "pt": "Melhor",
    },
    "on": {
        "en": "On",
        "ar": "مفعّل",
        "tr": "Açık",
        "es": "Activado",
        "ru": "Вкл.",
        "fr": "Activé",
        "de": "An",
        "pt": "Ativado",
    },
    "off": {
        "en": "Off",
        "ar": "معطّل",
        "tr": "Kapalı",
        "es": "Desactivado",
        "ru": "Выкл.",
        "fr": "Désactivé",
        "de": "Aus",
        "pt": "Desativado",
    },
    "auto_on": {
        "en": "Auto-download: On",
        "ar": "التنزيل التلقائي: مفعّل",
        "tr": "Otomatik indirme: Açık",
        "es": "Descarga automática: Activada",
        "ru": "Автозагрузка: Вкл.",
        "fr": "Téléchargement auto : Activé",
        "de": "Auto-Download: An",
        "pt": "Download automático: Ativado",
    },
    "auto_off": {
        "en": "Auto-download: Off",
        "ar": "التنزيل التلقائي: معطّل",
        "tr": "Otomatik indirme: Kapalı",
        "es": "Descarga automática: Desactivada",
        "ru": "Автозагрузка: Выкл.",
        "fr": "Téléchargement auto : Désactivé",
        "de": "Auto-Download: Aus",
        "pt": "Download automático: Desativado",
    },
    # --- Errors (user-safe, short) ---
    "error_generic": {
        "en": "😕 Something went wrong. Please try again.",
        "ar": "😕 حدث خطأ ما. حاول مرة أخرى.",
        "tr": "😕 Bir şeyler ters gitti. Lütfen tekrar deneyin.",
        "es": "😕 Algo salió mal. Inténtalo de nuevo.",
        "ru": "😕 Что-то пошло не так. Попробуйте ещё раз.",
        "fr": "😕 Une erreur est survenue. Veuillez réessayer.",
        "de": "😕 Etwas ist schiefgelaufen. Bitte versuche es erneut.",
        "pt": "😕 Algo deu errado. Tente novamente.",
    },
    "error_probe_unsupported": {
        "en": "😕 That site isn't supported by yt-dlp.",
        "ar": "😕 هذا الموقع غير مدعوم في yt-dlp.",
        "tr": "😕 Bu site yt-dlp tarafından desteklenmiyor.",
        "es": "😕 Ese sitio no es compatible con yt-dlp.",
        "ru": "😕 Этот сайт не поддерживается yt-dlp.",
        "fr": "😕 Ce site n'est pas pris en charge par yt-dlp.",
        "de": "😕 Diese Seite wird von yt-dlp nicht unterstützt.",
        "pt": "😕 Esse site não é suportado pelo yt-dlp.",
    },
    "error_probe_private": {
        "en": "🔒 That video is private or requires login.",
        "ar": "🔒 هذا الفيديو خاص أو يتطلب تسجيل الدخول.",
        "tr": "🔒 Bu video özeldir veya giriş gerektirir.",
        "es": "🔒 Ese video es privado o requiere iniciar sesión.",
        "ru": "🔒 Это видео приватное или требует входа.",
        "fr": "🔒 Cette vidéo est privée ou nécessite une connexion.",
        "de": "🔒 Dieses Video ist privat oder erfordert eine Anmeldung.",
        "pt": "🔒 Esse vídeo é privado ou requer login.",
    },
    "error_probe_region": {
        "en": "🌍 That video is region-locked.",
        "ar": "🌍 هذا الفيديو مقيد جغرافياً.",
        "tr": "🌍 Bu video bölge kısıtlamalıdır.",
        "es": "🌍 Ese video está bloqueado por región.",
        "ru": "🌍 Это видео ограничено по региону.",
        "fr": "🌍 Cette vidéo est verrouillée par région.",
        "de": "🌍 Dieses Video ist regionsgesperrt.",
        "pt": "🌍 Esse vídeo está bloqueado por região.",
    },
    "error_probe_unavailable": {
        "en": "🚫 That video is unavailable.",
        "ar": "🚫 هذا الفيديو غير متوفر.",
        "tr": "🚫 Bu video kullanılamıyor.",
        "es": "🚫 Ese video no está disponible.",
        "ru": "🚫 Это видео недоступно.",
        "fr": "🚫 Cette vidéo est indisponible.",
        "de": "🚫 Dieses Video ist nicht verfügbar.",
        "pt": "🚫 Esse vídeo está indisponível.",
    },
    "error_probe_timeout": {
        "en": "⏱️ Timed out fetching info. Try again.",
        "ar": "⏱️ انتهت المهلة أثناء جلب المعلومات. حاول مرة أخرى.",
        "tr": "⏱️ Bilgi alınırken zaman aşımı. Tekrar deneyin.",
        "es": "⏱️ Se agotó el tiempo al obtener la información. Inténtalo de nuevo.",
        "ru": "⏱️ Истекло время получения информации. Попробуйте ещё раз.",
        "fr": "⏱️ Délai dépassé lors de la récupération. Réessayez.",
        "de": "⏱️ Zeitüberschreitung beim Abrufen der Infos. Versuche es erneut.",
        "pt": "⏱️ Tempo esgotado ao buscar informações. Tente novamente.",
    },
    # --- Misc ---
    "debug_id": {
        "en": "🔧 Your Telegram user ID is {id}",
        "ar": "🔧 معرف مستخدم تيليجرام الخاص بك هو {id}",
        "tr": "🔧 Telegram kullanıcı kimliğiniz: {id}",
        "es": "🔧 Tu ID de usuario de Telegram es {id}",
        "ru": "🔧 Ваш ID пользователя Telegram: {id}",
        "fr": "🔧 Votre ID utilisateur Telegram est {id}",
        "de": "🔧 Deine Telegram-Benutzer-ID ist {id}",
        "pt": "🔧 Seu ID de usuário do Telegram é {id}",
    },
}


def known_keys() -> set[str]:
    return set(STRINGS)


def t(lang: str | None, key: str, **kwargs) -> str:
    """Return the localized string for ``key`` in ``lang`` (falls back to English).

    Unknown languages fall back to the default; unknown keys raise ``KeyError`` so a
    missing translation is a loud bug during development, not a silent ``{key}`` leak.
    """
    entry = STRINGS.get(key)
    if entry is None:
        raise KeyError(f"Unknown i18n key: {key!r}")
    lang = lang if lang in LANGS else FALLBACK_LANG
    text = entry.get(lang) or entry.get(FALLBACK_LANG) or ""
    if kwargs:
        text = text.format(**kwargs)
    return text


def user_display(username: str | None, user_id: int) -> str:
    """Canonical requestor label: the username, or ``user:<id>`` if there is none."""
    if username:
        return username.strip().lower()
    return f"user:{user_id}"


class LanguageStore:
    """Per-user language preference, persisted in the shared SQLite database."""

    def __init__(self, data_dir: str | Path, default: str = "en"):
        self.data_dir = Path(data_dir)
        self.default = default if default in LANGS else "en"
        self._legacy_json = self.data_dir / "langs.json"
        self._conn: sqlite3.Connection | None = None

    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            conn = connect(self.data_dir)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS langs ("
                " user_id TEXT PRIMARY KEY,"
                " lang TEXT NOT NULL"
                ")"
            )
            conn.commit()
            self._conn = conn
            self._import_legacy_json()
        return self._conn

    def _import_legacy_json(self) -> None:
        """One-time import of a pre-SQLite ``langs.json`` file, then remove it."""
        legacy = self._legacy_json
        if not legacy.exists():
            return
        try:
            raw = json.loads(legacy.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return  # corrupt -> leave it, fall back to defaults
        if not isinstance(raw, dict):
            return
        assert self._conn is not None
        for k, v in raw.items():
            if v in LANGS:
                self._conn.execute(
                    "INSERT OR REPLACE INTO langs (user_id, lang) VALUES (?, ?)",
                    (str(k), v),
                )
        self._conn.commit()
        legacy.unlink(missing_ok=True)

    def is_set(self, user_id: int) -> bool:
        row = (
            self._db()
            .execute("SELECT 1 FROM langs WHERE user_id = ?", (str(user_id),))
            .fetchone()
        )
        return row is not None

    def get(self, user_id: int) -> str:
        row = (
            self._db()
            .execute("SELECT lang FROM langs WHERE user_id = ?", (str(user_id),))
            .fetchone()
        )
        return row[0] if row else self.default

    def set(self, user_id: int, lang: str) -> None:
        if lang not in LANGS:
            raise ValueError(f"Invalid language: {lang!r}")
        conn = self._db()
        conn.execute(
            "INSERT OR REPLACE INTO langs (user_id, lang) VALUES (?, ?)",
            (str(user_id), lang),
        )
        conn.commit()

    def detect(self, client_lang: str | None) -> str:
        """Map a Telegram client language code to our supported set.

        Handles region-qualified codes (e.g. ``pt-BR`` → ``pt``) before falling back
        to the default.
        """
        code = (client_lang or "").strip().lower().replace("-", "_")
        base = code.split("_", 1)[0]
        if code in LANGS:
            return code
        if base in LANGS:
            return base
        return self.default


def make_store(data_dir: str | Path, default: str = "en") -> LanguageStore:
    return LanguageStore(data_dir, default)


# Re-exported so tests can import a single helper for a fresh store.
def open_store(data_dir: str | Path, default: str = "en") -> LanguageStore:
    return LanguageStore(data_dir, default)


# Silence unused-import lint for Mapping (kept for type documentation parity).
_ = Mapping
