import SwiftUI

struct RoomListView: View {
    @EnvironmentObject private var roomStore: RoomStore
    @State private var showingScanner = false

    var body: some View {
        NavigationStack {
            Group {
                if roomStore.rooms.isEmpty {
                    ContentUnavailableView(
                        "No saved rooms",
                        systemImage: "cube.transparent",
                        description: Text("Scan a room to build your first reusable spatial baseline.")
                    )
                } else {
                    List {
                        ForEach(roomStore.rooms) { room in
                            VStack(alignment: .leading, spacing: 5) {
                                Text(room.name).font(.headline)
                                Text("\(room.objects.count) detected objects · \(room.capturedAt.formatted(date: .abbreviated, time: .shortened))")
                                    .font(.subheadline)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        .onDelete { offsets in
                            offsets.map { roomStore.rooms[$0] }.forEach(roomStore.delete)
                        }
                    }
                }
            }
            .navigationTitle("Theia")
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button { showingScanner = true } label: {
                        Label("Scan room", systemImage: "camera.viewfinder")
                    }
                }
            }
            .sheet(isPresented: $showingScanner) {
                RoomScannerView()
                    .environmentObject(roomStore)
            }
        }
    }
}
