import Foundation
import Vision

enum OCRError: Error {
    case missingImagePath
    case invalidImagePath
}

let arguments = CommandLine.arguments
guard arguments.count == 2 else {
    throw OCRError.missingImagePath
}

let imageURL = URL(fileURLWithPath: arguments[1])
guard FileManager.default.fileExists(atPath: imageURL.path) else {
    throw OCRError.invalidImagePath
}

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true
request.recognitionLanguages = ["sv-SE", "en-US"]

let handler = VNImageRequestHandler(url: imageURL, options: [:])
try handler.perform([request])

let lines = (request.results ?? [])
    .compactMap { $0.topCandidates(1).first?.string }

let payload: [String: Any] = ["text": lines.joined(separator: "\n")]
let json = try JSONSerialization.data(withJSONObject: payload, options: [])
FileHandle.standardOutput.write(json)
