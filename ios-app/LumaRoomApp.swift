import SwiftUI

@main
struct TheiaApp: App {
    @StateObject private var roomStore = RoomStore()

    var body: some Scene {
        WindowGroup {
            RoomListView()
                .environmentObject(roomStore)
        }
    }
}
