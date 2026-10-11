# Jarvis Voice for Mac. Needs Xcode 26+ and XcodeGen (brew install xcodegen).
PROJECT  := JarvisVoiceMac.xcodeproj
SCHEME   := JarvisVoiceMac
DERIVED  := build
APP      := $(DERIVED)/Build/Products/Debug/Jarvis Voice.app
RELEASE_APP := $(DERIVED)/Build/Products/Release/Jarvis Voice.app

# With Local.xcconfig (DEVELOPMENT_TEAM) the build is signed with a stable identity.
# Without it, compile unsigned: good for CI and a compile check, not for installing.
ifeq ($(wildcard Local.xcconfig),)
SIGNING := CODE_SIGNING_ALLOWED=NO CODE_SIGN_IDENTITY=-
else
SIGNING := -allowProvisioningUpdates
endif

.PHONY: generate build test release install clean

generate:
	xcodegen generate

build: generate
	xcodebuild -project $(PROJECT) -scheme $(SCHEME) -configuration Debug \
	  -derivedDataPath $(DERIVED) $(SIGNING) build

test: generate
	xcodebuild -project $(PROJECT) -scheme $(SCHEME) -configuration Debug \
	  -derivedDataPath $(DERIVED) $(SIGNING) test

# What the macOS Release workflow ships: Release, ad-hoc signed ("Sign to Run Locally") with the
# entitlements. A sealed signature lets macOS keep permission grants (until the next update); a
# bundle with no signature of its own is asked for the microphone again and again.
release: generate
	xcodebuild -project $(PROJECT) -scheme $(SCHEME) -configuration Release \
	  -destination 'platform=macOS' -derivedDataPath $(DERIVED) \
	  CODE_SIGN_STYLE=Manual CODE_SIGN_IDENTITY=- DEVELOPMENT_TEAM= build
	codesign --verify --deep --strict "$(RELEASE_APP)"
	codesign -d --entitlements - "$(RELEASE_APP)" 2>/dev/null | grep -q com.apple.security.device.audio-input

install: build
	@[ -f Local.xcconfig ] || { echo "error: refusing to install an unsigned build; create Local.xcconfig first"; exit 1; }
	mkdir -p $(HOME)/Applications
	rm -rf "$(HOME)/Applications/Jarvis Voice.app"
	cp -R "$(APP)" "$(HOME)/Applications/"
	@echo "Installed to ~/Applications/Jarvis Voice.app"

clean:
	rm -rf $(DERIVED) $(PROJECT)
