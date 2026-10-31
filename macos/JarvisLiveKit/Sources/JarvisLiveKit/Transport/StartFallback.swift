import Foundation

/// When a session that was meant to stream starts recording on this Mac instead.
///
/// Local-only is for when the server is unavailable: if creating the session fails because the
/// server can't be reached or is failing, the meeting is still recorded, and uploaded later.
/// Anything that says the request itself is wrong (a rejected token, an unknown session) is not an
/// outage and is shown to the user instead.
public enum StartFallback {
    public static func shouldFallBack(_ error: Error) -> Bool {
        if let urlError = error as? URLError { return urlError.code != .cancelled }
        guard let apiError = error as? LiveAPIError else { return false }
        switch apiError {
        case .http(let status, _): return status >= 500
        case .invalidResponse: return true
        }
    }
}
