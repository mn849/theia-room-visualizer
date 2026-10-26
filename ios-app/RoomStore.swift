import Foundation

@MainActor
final class RoomStore: ObservableObject {
    @Published private(set) var rooms: [RoomModel] = []

    private let directory: URL
    private let encoder: JSONEncoder
    private let decoder: JSONDecoder

    init(fileManager: FileManager = .default) {
        let documents = fileManager.urls(for: .documentDirectory, in: .userDomainMask)[0]
        self.directory = documents.appendingPathComponent("Theia", isDirectory: true)
        self.encoder = JSONEncoder()
        self.decoder = JSONDecoder()
        encoder.dateEncodingStrategy = .iso8601
        decoder.dateDecodingStrategy = .iso8601
        try? fileManager.createDirectory(at: directory, withIntermediateDirectories: true)
        load()
    }

    func save(_ room: RoomModel) {
        let url = directory.appendingPathComponent("\(room.id.uuidString).json")
        do {
            try encoder.encode(room).write(to: url, options: .atomic)
            rooms.removeAll { $0.id == room.id }
            rooms.append(room)
            rooms.sort { $0.capturedAt > $1.capturedAt }
        } catch {
            print("Theia save failed: \(error)")
        }
    }

    func delete(_ room: RoomModel) {
        let url = directory.appendingPathComponent("\(room.id.uuidString).json")
        try? FileManager.default.removeItem(at: url)
        rooms.removeAll { $0.id == room.id }
    }

    func exportURL(for room: RoomModel) throws -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("\(room.name).json")
        try encoder.encode(room).write(to: url, options: .atomic)
        return url
    }

    private func load() {
        guard let files = try? FileManager.default.contentsOfDirectory(
            at: directory, includingPropertiesForKeys: nil
        ) else { return }
        rooms = files.compactMap { try? decoder.decode(RoomModel.self, from: Data(contentsOf: $0)) }
            .sorted { $0.capturedAt > $1.capturedAt }
    }
}
