// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "JarvisLiveKit",
    // String form: `.v26` needs tools-version 6.2; this is the same deployment target.
    platforms: [.macOS("26.0")],
    products: [
        .library(name: "JarvisLiveKit", targets: ["JarvisLiveKit"]),
    ],
    targets: [
        .target(name: "JarvisLiveKit"),
        .testTarget(name: "JarvisLiveKitTests", dependencies: ["JarvisLiveKit"]),
    ],
    swiftLanguageModes: [.v6]
)
