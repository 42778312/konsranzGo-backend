// swift-tools-version: 6.0
import PackageDescription

// The live server's API: Codable types and the trip key, Foundation only. The original is `KGLiveAPI/` in
// github.com/42778312/konsranzGo-backend; the app keeps an identical copy in `Packages/KGLiveAPI`. Change the
// original first, copy it over, and check with the backend's `scripts/check-api.sh`. Fields are only added to v1.
let package = Package(
    name: "KGLiveAPI",
    platforms: [.iOS(.v18), .macOS(.v15)],
    products: [.library(name: "KGLiveAPI", targets: ["KGLiveAPI"])],
    targets: [
        .target(name: "KGLiveAPI"),
        .testTarget(name: "KGLiveAPITests", dependencies: ["KGLiveAPI"]),
    ],
    swiftLanguageModes: [.v6]
)
