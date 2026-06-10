#!/usr/bin/env bash
# Downloads sherpa-onnx Kotlin bindings, native libs, and whisper-base.en model.
# Run from the repo root before first build.
# Flags:
#   --no-model   Skip the 145 MB whisper model download (CI use only)
set -euo pipefail

SKIP_MODEL=false
for arg in "$@"; do
  [[ "$arg" == "--no-model" ]] && SKIP_MODEL=true
done

SHERPA_VERSION="1.13.2"
MODEL_NAME="sherpa-onnx-whisper-base.en"
ASSETS_DIR="android/app/src/main/assets/models/whisper-base-en"
JNILIBS_DIR="android/app/src/main/jniLibs/arm64-v8a"
KOTLIN_DIR="android/app/src/main/java/com/k2fsa/sherpa/onnx"
TMP_DIR="$(mktemp -d)"

cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

mkdir -p "$ASSETS_DIR" "$JNILIBS_DIR" "$KOTLIN_DIR"

dl() {
  local url="$1" dest="$2"
  if command -v wget &>/dev/null; then
    wget -q --show-progress -O "$dest" "$url"
  else
    curl -L --progress-bar -o "$dest" "$url"
  fi
}

# ── Kotlin bindings ──────────────────────────────────────────────────────────
echo "=== sherpa-onnx Kotlin bindings (v${SHERPA_VERSION}) ==="
BASE="https://raw.githubusercontent.com/k2-fsa/sherpa-onnx/master/sherpa-onnx/kotlin-api"
for f in FeatureConfig OfflineRecognizer OfflineStream QnnConfig HomophoneReplacerConfig; do
  dest="$KOTLIN_DIR/$f.kt"
  if [ -f "$dest" ]; then
    echo "  $f.kt already present, skipping."
  else
    dl "$BASE/$f.kt" "$dest"
    echo "  $f.kt downloaded."
  fi
done

# ── Native libs ──────────────────────────────────────────────────────────────
echo ""
echo "=== sherpa-onnx native libs (arm64-v8a, v${SHERPA_VERSION}) ==="
TARBALL_URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/v${SHERPA_VERSION}/sherpa-onnx-v${SHERPA_VERSION}-android.tar.bz2"
TARBALL="$TMP_DIR/sherpa-android.tar.bz2"

if [ -f "$JNILIBS_DIR/libsherpa-onnx-jni.so" ]; then
  echo "  Native libs already present, skipping."
else
  dl "$TARBALL_URL" "$TARBALL"
  tar -xjf "$TARBALL" -C "$TMP_DIR/"
  cp "$TMP_DIR/jniLibs/arm64-v8a/libsherpa-onnx-jni.so" "$JNILIBS_DIR/"
  cp "$TMP_DIR/jniLibs/arm64-v8a/libonnxruntime.so"      "$JNILIBS_DIR/"
  cp "$TMP_DIR/jniLibs/arm64-v8a/libsherpa-onnx-c-api.so" "$JNILIBS_DIR/"
  echo "  .so files extracted to $JNILIBS_DIR"
fi

# ── whisper-base.en model ────────────────────────────────────────────────────
echo ""
if [ "$SKIP_MODEL" = "true" ]; then
  echo "=== whisper-base.en model SKIPPED (--no-model) ==="
else
  echo "=== whisper-base.en model ==="
  MODEL_URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/${MODEL_NAME}.tar.bz2"
  if [ -f "$ASSETS_DIR/base.en-encoder.int8.onnx" ] && [ -f "$ASSETS_DIR/base.en-tokens.txt" ]; then
    echo "  Model already present, skipping."
  else
    dl "$MODEL_URL" "$TMP_DIR/model.tar.bz2"
    tar -xjf "$TMP_DIR/model.tar.bz2" -C "$TMP_DIR/"
    cp "$TMP_DIR/${MODEL_NAME}/base.en-encoder.int8.onnx" "$ASSETS_DIR/"
    cp "$TMP_DIR/${MODEL_NAME}/base.en-decoder.int8.onnx" "$ASSETS_DIR/"
    cp "$TMP_DIR/${MODEL_NAME}/base.en-tokens.txt"        "$ASSETS_DIR/"
    echo "  Model saved to $ASSETS_DIR (~145 MB, git-ignored)"
  fi
fi

echo ""
echo "All done. Build with: cd android && ANDROID_HOME=~/Android/Sdk ./gradlew assembleDebug"
