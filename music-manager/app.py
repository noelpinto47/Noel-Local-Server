from pathlib import Path
import io
import os
import subprocess
import shutil
import uuid
import re

from dotenv import load_dotenv

from flask import (
    Flask,
    jsonify,
    render_template,
    request,
    send_file,
)

from PIL import Image
from mutagen import File
from mutagen.id3 import (
    APIC,
    ID3,
    ID3NoHeaderError,
    TALB,
    TCON,
    TDRC,
    TIT2,
    TPE1,
    TPE2,
    TRCK,
    TPOS,
    TXXX,
)


load_dotenv()

app = Flask(__name__)

# ---------------------------------------------------------
# Configuration
# ---------------------------------------------------------

MUSIC_FOLDER = Path(r"C:\server\music")

SUPPORTED_EXTENSIONS = {
    ".mp3",
    ".flac",
    ".m4a",
    ".aac",
    ".ogg",
    ".opus",
    ".wav",
}

MAX_ARTWORK_SIZE = 1600

NAVIDROME_CONTAINER = os.getenv("NAVIDROME_CONTAINER", "navidrome")

# Uploaded files are staged here until the import wizard is completed.
IMPORT_FOLDER = Path(r"C:\server\music-manager\imports")
IMPORT_FOLDER.mkdir(parents=True, exist_ok=True)

MAX_UPLOAD_SIZE = 500 * 1024 * 1024  # 500 MB
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_SIZE

def trigger_navidrome_scan():
    """
    Ask the running Navidrome Docker container to perform
    an incremental library scan.
    """
    try:
        result = subprocess.run(
            [
                "docker",
                "exec",
                NAVIDROME_CONTAINER,
                "navidrome",
                "scan",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode == 0:
            return {
                "success": True,
                "message": "Navidrome scan started successfully.",
                "output": result.stdout.strip(),
            }

        return {
            "success": False,
            "message": "Navidrome scan failed.",
            "output": result.stderr.strip(),
        }

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "message": "Navidrome scan timed out.",
            "output": "",
        }

    except Exception as e:
        return {
            "success": False,
            "message": f"Could not start Navidrome scan: {e}",
            "output": "",
        }

# ---------------------------------------------------------
# Security / path helpers
# ---------------------------------------------------------

def get_safe_path(relative_path):

    if not relative_path:
        return None

    try:

        file = (
            MUSIC_FOLDER / relative_path
        ).resolve()

        file.relative_to(
            MUSIC_FOLDER.resolve()
        )

    except ValueError:

        return None

    return file


def get_album_folder(relative_path):

    file = get_safe_path(relative_path)

    if not file or not file.is_file():
        return None

    return file.parent


# ---------------------------------------------------------
# Music scanning
# ---------------------------------------------------------

def get_music_files():

    if not MUSIC_FOLDER.exists():
        return []

    files = []

    for file in MUSIC_FOLDER.rglob("*"):

        if not file.is_file():
            continue

        if file.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue

        files.append(file)

    return sorted(
        files,
        key=lambda file: str(file).lower()
    )


# ---------------------------------------------------------
# Metadata
# ---------------------------------------------------------

def read_metadata(file):

    metadata = {
        "title": "",
        "artists": [],
        "album": "",
        "album_artists": [],
        "genre": "",
        "year": "",
        "track": "",
        "disc": "",
        "has_artwork": False,
        "has_cover": False,
        "cover_filename": None,
    }

    try:

        audio = File(file, easy=True)

        if audio:

            metadata["title"] = (
                audio.get("title", [""])[0]
                if audio.get("title")
                else ""
            )

            metadata["artists"] = [
                str(value)
                for value in audio.get("artist", [])
            ]

            metadata["album"] = (
                audio.get("album", [""])[0]
                if audio.get("album")
                else ""
            )

            metadata["album_artists"] = [
                str(value)
                for value in audio.get("albumartist", [])
            ]

            metadata["genre"] = (
                audio.get("genre", [""])[0]
                if audio.get("genre")
                else ""
            )

            metadata["year"] = (
                audio.get("date", [""])[0]
                if audio.get("date")
                else ""
            )

            metadata["track"] = (
                audio.get("tracknumber", [""])[0]
                if audio.get("tracknumber")
                else ""
            )

            metadata["disc"] = (
                audio.get("discnumber", [""])[0]
                if audio.get("discnumber")
                else ""
            )


        try:

            id3 = ID3(file)

            artist_tags = id3.getall(
                "TXXX:ARTISTS"
            )

            if artist_tags:

                metadata["artists"] = []

                for tag in artist_tags:

                    metadata["artists"].extend(
                        tag.text
                    )


            album_artist_tags = id3.getall(
                "TXXX:ALBUMARTISTS"
            )

            if album_artist_tags:

                metadata["album_artists"] = []

                for tag in album_artist_tags:

                    metadata["album_artists"].extend(
                        tag.text
                    )


            metadata["has_artwork"] = any(
                key.startswith("APIC")
                for key in id3.keys()
            )

        except Exception:

            pass


    except Exception as error:

        print(
            f"Could not read metadata from "
            f"{file}: {error}"
        )


    cover = find_cover(file.parent)
    metadata["has_cover"] = cover is not None
    metadata["cover_filename"] = cover.name if cover else None

    return metadata


# ---------------------------------------------------------
# Artwork helpers
# ---------------------------------------------------------

def find_cover(album_folder):

    if not album_folder:
        return None

    candidates = [
        "cover.jpg",
        "cover.jpeg",
        "cover.png",
        "folder.jpg",
        "folder.jpeg",
        "folder.png",
        "front.jpg",
        "front.jpeg",
        "front.png",
    ]

    for name in candidates:

        candidate = (
            album_folder / name
        )

        if candidate.is_file():

            return candidate

    return None


def save_cover_image(
    image_data,
    destination
):

    try:

        image = Image.open(
            io.BytesIO(image_data)
        )

        # Convert formats such as PNG/WebP
        # to JPEG.

        image = image.convert("RGB")

        # Prevent unnecessarily huge artwork.

        image.thumbnail(
            (
                MAX_ARTWORK_SIZE,
                MAX_ARTWORK_SIZE
            ),
            Image.Resampling.LANCZOS
        )

        image.save(
            destination,
            "JPEG",
            quality=92,
            optimize=True
        )

        return True

    except Exception as error:

        print(
            f"Could not save artwork: {error}"
        )

        return False



# ---------------------------------------------------------
# Import helpers
# ---------------------------------------------------------

def safe_component(value, fallback="Unknown"):
    """Create a safe Windows folder/file component."""
    value = str(value or "").strip()
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", value)
    value = re.sub(r"\s+", " ", value).strip()
    value = value.rstrip(". ")

    if not value:
        return fallback

    # Windows reserved device names.
    if value.upper() in {
        "CON", "PRN", "AUX", "NUL",
        "COM1", "COM2", "COM3", "COM4", "COM5",
        "COM6", "COM7", "COM8", "COM9",
        "LPT1", "LPT2", "LPT3", "LPT4", "LPT5",
        "LPT6", "LPT7", "LPT8", "LPT9",
    }:
        return f"_{value}"

    return value[:180]


def import_stage(stage_id):
    """Return a safe staging directory for an import."""
    if not stage_id or not re.fullmatch(r"[a-f0-9]{32}", stage_id):
        return None

    stage = (IMPORT_FOLDER / stage_id).resolve()

    try:
        stage.relative_to(IMPORT_FOLDER.resolve())
    except ValueError:
        return None

    return stage


def get_stage_audio(stage_id):
    stage = import_stage(stage_id)
    if not stage or not stage.is_dir():
        return None

    audio_files = [
        p for p in stage.iterdir()
        if p.is_file() and p.suffix.lower() == ".mp3"
    ]

    return audio_files[0] if audio_files else None


def read_import_defaults(audio_file):
    """Read existing tags from an uploaded MP3 as starting values."""
    metadata = read_metadata(audio_file)

    # A filename is a useful fallback for recordings with no tags.
    stem = audio_file.stem
    if not metadata["title"]:
        metadata["title"] = stem

    return metadata


def save_staged_artwork(image_data, destination):
    return save_cover_image(image_data, destination)


def build_import_destination(metadata):
    artists = metadata.get("artists") or []
    album_artists = metadata.get("album_artists") or []

    organization_artist = (
        " & ".join(album_artists)
        if album_artists
        else " & ".join(artists)
    )

    artist_folder = safe_component(organization_artist, "Unknown Artist")
    album_folder = safe_component(
        metadata.get("album"),
        "Unknown Album"
    )

    title = safe_component(
        metadata.get("title"),
        "Unknown Title"
    )

    track = str(metadata.get("track") or "").strip()
    disc = str(metadata.get("disc") or "").strip()

    # Keep filenames consistent while avoiding duplicate prefixes.
    prefix = ""
    if track:
        try:
            track_number = int(track.split("/")[0])
            prefix = f"{track_number:02d} - "
        except ValueError:
            prefix = ""

    if disc:
        try:
            disc_number = int(disc.split("/")[0])
            if disc_number > 1:
                prefix = f"D{disc_number}-{prefix}"
        except ValueError:
            pass

    filename = f"{prefix}{title}.mp3"

    album_dir = MUSIC_FOLDER / artist_folder / album_folder
    destination = album_dir / filename

    return album_dir, destination


def write_mp3_metadata(file, metadata):
    """Write the same Navidrome-friendly tags used by the library editor."""
    title = str(metadata.get("title") or "").strip()
    artists = metadata.get("artists") or []
    album = str(metadata.get("album") or "").strip()
    album_artists = metadata.get("album_artists") or []
    genre = str(metadata.get("genre") or "").strip()
    year = str(metadata.get("year") or "").strip()
    track = str(metadata.get("track") or "").strip()
    disc = str(metadata.get("disc") or "").strip()

    if file.suffix.lower() != ".mp3":
        raise ValueError("Only MP3 imports are supported by the import wizard.")

    try:
        tags = ID3(file)
    except ID3NoHeaderError:
        tags = ID3()

    for tag_name in (
        "TIT2", "TPE1", "TPE2", "TALB",
        "TCON", "TDRC", "TRCK", "TPOS",
    ):
        tags.delall(tag_name)

    tags.delall("TXXX:ARTISTS")
    tags.delall("TXXX:ALBUMARTISTS")

    if title:
        tags.add(TIT2(encoding=3, text=title))

    if artists:
        tags.add(
            TPE1(
                encoding=3,
                text=" feat. ".join(artists),
            )
        )
        tags.add(
            TXXX(
                encoding=3,
                desc="ARTISTS",
                text=artists,
            )
        )

    if album:
        tags.add(TALB(encoding=3, text=album))

    if album_artists:
        tags.add(
            TPE2(
                encoding=3,
                text=" & ".join(album_artists),
            )
        )
        tags.add(
            TXXX(
                encoding=3,
                desc="ALBUMARTISTS",
                text=album_artists,
            )
        )

    if genre:
        tags.add(TCON(encoding=3, text=genre))

    if year:
        tags.add(TDRC(encoding=3, text=year))

    if track:
        tags.add(TRCK(encoding=3, text=track))

    if disc:
        tags.add(TPOS(encoding=3, text=disc))

    tags.save(file, v2_version=4)


def validate_import_metadata(metadata):
    required = {
        "title": "Title",
        "album": "Album",
    }

    for key, label in required.items():
        if not str(metadata.get(key) or "").strip():
            return f"{label} is required."

    artists = metadata.get("artists")
    if not isinstance(artists, list) or not any(
        str(x).strip() for x in artists
    ):
        return "At least one artist is required."

    album_artists = metadata.get("album_artists")
    if not isinstance(album_artists, list) or not any(
        str(x).strip() for x in album_artists
    ):
        return "At least one album artist is required."

    return None


# ---------------------------------------------------------
# Import API
# ---------------------------------------------------------

@app.route("/api/import/upload", methods=["POST"])
def import_upload():
    uploaded = request.files.get("music")

    if not uploaded or not uploaded.filename:
        return jsonify({"error": "No MP3 file was uploaded."}), 400

    original_name = Path(uploaded.filename).name

    if Path(original_name).suffix.lower() != ".mp3":
        return jsonify({"error": "Only MP3 files are supported by the import wizard."}), 400

    stage_id = uuid.uuid4().hex
    stage = IMPORT_FOLDER / stage_id
    stage.mkdir(parents=True, exist_ok=True)

    audio_path = stage / original_name

    try:
        uploaded.save(audio_path)

        # Validate that the upload is actually readable as an MP3.
        try:
            test_audio = File(audio_path)
            if test_audio is None:
                raise ValueError("The uploaded file could not be read as audio.")
        except Exception as error:
            shutil.rmtree(stage, ignore_errors=True)
            return jsonify({
                "error": f"Invalid MP3 file: {error}"
            }), 400

        metadata = read_import_defaults(audio_path)

        return jsonify({
            "success": True,
            "stage_id": stage_id,
            "filename": original_name,
            "size": audio_path.stat().st_size,
            "metadata": metadata,
        })

    except Exception as error:
        shutil.rmtree(stage, ignore_errors=True)
        return jsonify({
            "error": f"Could not upload MP3: {error}"
        }), 500


@app.route("/api/import/artwork", methods=["POST"])
def import_artwork():
    stage_id = request.form.get("stage_id")
    stage = import_stage(stage_id)

    if not stage or not stage.is_dir():
        return jsonify({"error": "Import session not found."}), 404

    uploaded = request.files.get("artwork")

    if not uploaded:
        return jsonify({"error": "No artwork image was uploaded."}), 400

    image_data = uploaded.read()

    if not image_data:
        return jsonify({"error": "The uploaded image is empty."}), 400

    try:
        image = Image.open(io.BytesIO(image_data))
        image.verify()
    except Exception:
        return jsonify({
            "error": "The uploaded file is not a valid image."
        }), 400

    destination = stage / "cover.jpg"

    if not save_staged_artwork(image_data, destination):
        return jsonify({"error": "Could not save artwork."}), 500

    return jsonify({
        "success": True,
        "artwork": f"/api/import/artwork/{stage_id}",
    })


@app.route("/api/import/artwork/<stage_id>")
def import_artwork_preview(stage_id):
    stage = import_stage(stage_id)

    if not stage:
        return jsonify({"error": "Import session not found."}), 404

    cover = stage / "cover.jpg"

    if not cover.is_file():
        return jsonify({"error": "No artwork found."}), 404

    return send_file(cover, mimetype="image/jpeg")


@app.route("/api/import/complete", methods=["POST"])
def import_complete():
    data = request.get_json(silent=True) or {}

    stage_id = data.get("stage_id")
    stage = import_stage(stage_id)
    audio_path = get_stage_audio(stage_id)

    if not stage or not stage.is_dir() or not audio_path:
        return jsonify({"error": "Import session not found."}), 404

    metadata = data.get("metadata") or {}

    # Normalize arrays.
    for key in ("artists", "album_artists"):
        value = metadata.get(key, [])
        if not isinstance(value, list):
            value = [value]
        metadata[key] = [
            str(item).strip()
            for item in value
            if str(item).strip()
        ]

    for key in ("title", "album", "genre", "year", "track", "disc"):
        metadata[key] = str(metadata.get(key) or "").strip()

    validation_error = validate_import_metadata(metadata)
    if validation_error:
        return jsonify({"error": validation_error}), 400

    album_dir, destination = build_import_destination(metadata)

    if destination.exists():
        return jsonify({
            "error": (
                "A file with this destination already exists: "
                f"{destination.relative_to(MUSIC_FOLDER)}"
            )
        }), 409

    album_dir.mkdir(parents=True, exist_ok=True)

    try:
        shutil.copy2(audio_path, destination)
        write_mp3_metadata(destination, metadata)

        staged_cover = stage / "cover.jpg"
        if staged_cover.is_file():
            shutil.copy2(
                staged_cover,
                album_dir / "cover.jpg",
            )

        # One scan after the complete import.
        navidrome_scan = trigger_navidrome_scan()

        relative_destination = str(
            destination.relative_to(MUSIC_FOLDER)
        )

        shutil.rmtree(stage, ignore_errors=True)

        return jsonify({
            "success": True,
            "message": "Music imported successfully.",
            "path": relative_destination,
            "destination": relative_destination,
            "navidrome_scan": navidrome_scan,
        })

    except Exception as error:
        # Don't leave a partial music file behind.
        try:
            if destination.exists():
                destination.unlink()
        except Exception:
            pass

        return jsonify({
            "error": f"Could not complete import: {error}"
        }), 500


@app.route("/api/import/cancel", methods=["POST"])
def import_cancel():
    data = request.get_json(silent=True) or {}
    stage = import_stage(data.get("stage_id"))

    if stage and stage.exists():
        shutil.rmtree(stage, ignore_errors=True)

    return jsonify({"success": True})


# ---------------------------------------------------------
# Pages
# ---------------------------------------------------------

@app.route("/")
def index():

    return render_template(
        "index.html"
    )


# ---------------------------------------------------------
# Music API
# ---------------------------------------------------------

@app.route("/api/music")
def music():

    songs = []

    for file in get_music_files():

        relative_path = str(
            file.relative_to(
                MUSIC_FOLDER
            )
        )

        songs.append({

            "filename": file.name,

            "path": relative_path,

            "extension":
                file.suffix.lower(),

            "metadata":
                read_metadata(file),

        })


    return jsonify(songs)


@app.route(
    "/api/music/<path:relative_path>"
)
def music_detail(relative_path):

    file = get_safe_path(
        relative_path
    )

    if not file or not file.is_file():

        return jsonify({
            "error":
                "Music file not found."
        }), 404


    return jsonify({

        "filename": file.name,

        "path": str(
            file.relative_to(
                MUSIC_FOLDER
            )
        ),

        "extension":
            file.suffix.lower(),

        "metadata":
            read_metadata(file),

    })


# ---------------------------------------------------------
# Metadata update
# ---------------------------------------------------------

@app.route(
    "/api/music/update",
    methods=["POST"]
)
def update_music():

    data = request.get_json(
        silent=True
    )

    if not data:

        return jsonify({
            "error":
                "Invalid request."
        }), 400


    relative_path = data.get(
        "path"
    )

    file = get_safe_path(
        relative_path
    )

    if not file or not file.is_file():

        return jsonify({
            "error":
                "Music file not found."
        }), 404


    title = str(
        data.get(
            "title",
            ""
        )
    ).strip()


    artists = data.get(
        "artists",
        []
    )

    if not isinstance(
        artists,
        list
    ):

        artists = [
            str(artists)
        ]


    artists = [
        str(artist).strip()
        for artist in artists
        if str(artist).strip()
    ]


    album = str(
        data.get(
            "album",
            ""
        )
    ).strip()


    album_artists = data.get(
        "album_artists",
        []
    )

    if not isinstance(
        album_artists,
        list
    ):

        album_artists = [
            str(album_artists)
        ]


    album_artists = [
        str(artist).strip()
        for artist in album_artists
        if str(artist).strip()
    ]


    genre = str(
        data.get(
            "genre",
            ""
        )
    ).strip()


    year = str(
        data.get(
            "year",
            ""
        )
    ).strip()


    track = str(
        data.get(
            "track",
            ""
        )
    ).strip()


    disc = str(
        data.get(
            "disc",
            ""
        )
    ).strip()


    # -----------------------------------------------------
    # MP3
    # -----------------------------------------------------

    if file.suffix.lower() == ".mp3":

        try:

            tags = ID3(file)

        except ID3NoHeaderError:

            tags = ID3()


        tags.delall("TIT2")
        tags.delall("TPE1")
        tags.delall("TPE2")
        tags.delall("TALB")
        tags.delall("TCON")
        tags.delall("TDRC")
        tags.delall("TRCK")
        tags.delall("TPOS")

        tags.delall("TXXX:ARTISTS")
        tags.delall("TXXX:ALBUMARTISTS")


        if title:

            tags.add(
                TIT2(
                    encoding=3,
                    text=title
                )
            )


        if artists:

            display_artist = (
                " feat. ".join(artists)
            )

            tags.add(
                TPE1(
                    encoding=3,
                    text=display_artist
                )
            )

            tags.add(
                TXXX(
                    encoding=3,
                    desc="ARTISTS",
                    text=artists
                )
            )


        if album:

            tags.add(
                TALB(
                    encoding=3,
                    text=album
                )
            )


        if album_artists:

            display_album_artist = (
                " & ".join(
                    album_artists
                )
            )

            tags.add(
                TPE2(
                    encoding=3,
                    text=display_album_artist
                )
            )

            tags.add(
                TXXX(
                    encoding=3,
                    desc="ALBUMARTISTS",
                    text=album_artists
                )
            )


        if genre:

            tags.add(
                TCON(
                    encoding=3,
                    text=genre
                )
            )


        if year:

            tags.add(
                TDRC(
                    encoding=3,
                    text=year
                )
            )


        if track:

            tags.add(
                TRCK(
                    encoding=3,
                    text=track
                )
            )


        if disc:

            tags.add(
                TPOS(
                    encoding=3,
                    text=disc
                )
            )


        tags.save(
            file,
            v2_version=4
        )


    else:

        audio = File(
            file,
            easy=True
        )

        if audio is None:

            return jsonify({
                "error":
                    "This audio format cannot "
                    "be edited yet."
            }), 400


        audio["title"] = title
        audio["artist"] = artists
        audio["album"] = album
        audio["albumartist"] = album_artists
        audio["genre"] = genre
        audio["date"] = year
        audio["tracknumber"] = track
        audio["discnumber"] = disc

        audio.save()

    navidrome_scan = trigger_navidrome_scan()

    return jsonify({
        "success": True,
        "message": "Metadata updated successfully.",
        "navidrome_scan": navidrome_scan,
    })


# ---------------------------------------------------------
# Artwork API
# ---------------------------------------------------------

@app.route(
    "/api/artwork/<path:relative_path>"
)
def artwork(relative_path):

    file = get_safe_path(
        relative_path
    )

    if not file or not file.is_file():

        return jsonify({
            "error":
                "Music file not found."
        }), 404


    album_folder = file.parent

    cover = find_cover(
        album_folder
    )

    if not cover:

        return jsonify({
            "error":
                "No album artwork found."
        }), 404


    return send_file(
        cover,
        mimetype="image/jpeg"
    )


@app.route(
    "/api/artwork/upload",
    methods=["POST"]
)
def upload_artwork():

    relative_path = request.form.get(
        "path"
    )

    file = get_safe_path(
        relative_path
    )

    if not file or not file.is_file():

        return jsonify({
            "error":
                "Music file not found."
        }), 404


    uploaded = request.files.get(
        "artwork"
    )

    if not uploaded:

        return jsonify({
            "error":
                "No artwork image was uploaded."
        }), 400


    image_data = uploaded.read()

    if not image_data:

        return jsonify({
            "error":
                "The uploaded image is empty."
        }), 400


    # Validate before modifying anything.

    try:

        image = Image.open(
            io.BytesIO(image_data)
        )

        image.verify()

    except Exception:

        return jsonify({
            "error":
                "The uploaded file is not "
                "a valid image."
        }), 400


    album_folder = file.parent

    destination = (
        album_folder / "cover.jpg"
    )


    if not save_cover_image(
        image_data,
        destination
    ):

        return jsonify({
            "error":
                "Could not save artwork."
        }), 500


    navidrome_scan = trigger_navidrome_scan()

    return jsonify({
        "success": True,
        "artwork": f"/api/artwork/{relative_path}",
        "navidrome_scan": navidrome_scan,
    })


@app.route(
    "/api/artwork/apply",
    methods=["POST"]
)
def apply_artwork():

    data = request.get_json(
        silent=True
    )

    if not data:

        return jsonify({
            "error":
                "Invalid request."
        }), 400


    relative_path = data.get(
        "path"
    )

    file = get_safe_path(
        relative_path
    )

    if not file or not file.is_file():

        return jsonify({
            "error":
                "Music file not found."
        }), 404


    source = get_safe_path(
        data.get(
            "source",
            ""
        )
    )

    if not source or not source.is_file():

        return jsonify({
            "error":
                "Artwork source not found."
        }), 404


    album_folder = file.parent

    destination = (
        album_folder / "cover.jpg"
    )


    try:

        image_data = source.read_bytes()

        if not save_cover_image(
            image_data,
            destination
        ):

            return jsonify({
                "error":
                    "Could not create album artwork."
            }), 500

    except Exception as error:

        return jsonify({
            "error": str(error)
        }), 500


    navidrome_scan = trigger_navidrome_scan()

    return jsonify({
        "success": True,
        "navidrome_scan": navidrome_scan,
    })


# ---------------------------------------------------------
# Run
# ---------------------------------------------------------

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=8083,
        debug=True
    )