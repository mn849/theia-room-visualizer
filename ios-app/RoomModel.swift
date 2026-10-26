import Foundation
import RoomPlan

struct RoomModel: Codable, Identifiable {
    static let currentSchemaVersion = 1

    let id: UUID
    var name: String
    let capturedAt: Date
    let schemaVersion: Int
    let dimensions: Dimensions
    let objects: [DetectedObject]

    struct Dimensions: Codable {
        let widthMeters: Double?
        let depthMeters: Double?
        let heightMeters: Double?
    }

    struct DetectedObject: Codable, Identifiable {
        let id: UUID
        let category: String
        let dimensions: Dimensions
        let confidence: String
    }

    init(name: String, capturedRoom: CapturedRoom) {
        self.id = UUID()
        self.name = name
        self.capturedAt = Date()
        self.schemaVersion = Self.currentSchemaVersion
        self.dimensions = Dimensions(
            widthMeters: RoomModel.roomWidth(capturedRoom),
            depthMeters: RoomModel.roomDepth(capturedRoom),
            heightMeters: RoomModel.roomHeight(capturedRoom)
        )
        self.objects = capturedRoom.objects.map { object in
            let size = object.dimensions
            return DetectedObject(
                id: UUID(),
                category: String(describing: object.category),
                dimensions: Dimensions(
                    widthMeters: Double(size.x),
                    depthMeters: Double(size.z),
                    heightMeters: Double(size.y)
                ),
                confidence: String(describing: object.confidence)
            )
        }
    }

    private static func roomWidth(_ room: CapturedRoom) -> Double? {
        guard let bounds = room.walls.map(\.dimensions.x).max() else { return nil }
        return Double(bounds)
    }

    private static func roomDepth(_ room: CapturedRoom) -> Double? {
        guard let bounds = room.walls.map(\.dimensions.z).max() else { return nil }
        return Double(bounds)
    }

    private static func roomHeight(_ room: CapturedRoom) -> Double? {
        guard let height = room.walls.map(\.dimensions.y).max() else { return nil }
        return Double(height)
    }
}
