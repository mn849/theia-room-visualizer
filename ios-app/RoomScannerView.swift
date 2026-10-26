import SwiftUI
import RoomPlan

struct RoomScannerView: View {
    @Environment(\.dismiss) private var dismiss
    @EnvironmentObject private var roomStore: RoomStore
    @State private var capturedRoom: CapturedRoom?
    @State private var showingReview = false
    @State private var errorMessage: String?

    var body: some View {
        NavigationStack {
            RoomCaptureContainer(
                onCapture: { room in
                    capturedRoom = room
                    showingReview = true
                },
                onError: { errorMessage = error.localizedDescription }
            )
            .ignoresSafeArea()
            .navigationTitle("Scan room")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
            }
            .sheet(isPresented: $showingReview) {
                if let capturedRoom {
                    RoomReviewView(capturedRoom: capturedRoom) { name in
                        roomStore.save(RoomModel(name: name, capturedRoom: capturedRoom))
                        dismiss()
                    }
                }
            }
            .alert("Scan unavailable", isPresented: .constant(errorMessage != nil)) {
                Button("OK") { errorMessage = nil }
            } message: {
                Text(errorMessage ?? "")
            }
        }
    }
}

private struct RoomCaptureContainer: UIViewControllerRepresentable {
    let onCapture: (CapturedRoom) -> Void
    let onError: (Error) -> Void

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeUIViewController(context: Context) -> RoomCaptureViewController {
        let controller = RoomCaptureViewController()
        controller.delegate = context.coordinator
        return controller
    }

    func updateUIViewController(_ controller: RoomCaptureViewController, context: Context) {}

    final class Coordinator: NSObject, RoomCaptureViewControllerDelegate {
        let parent: RoomCaptureContainer
        init(_ parent: RoomCaptureContainer) { self.parent = parent }

        func captureView(shouldPresent roomDataForProcessing: CapturedRoomData, error: Error?) -> Bool {
            if let error { parent.onError(error); return false }
            return true
        }

        func captureView(didPresent processedResult: CapturedRoom, error: Error?) {
            if let error { parent.onError(error) }
            else { parent.onCapture(processedResult) }
        }
    }
}

private struct RoomReviewView: View {
    let capturedRoom: CapturedRoom
    let onSave: (String) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var name = "Living room"

    var body: some View {
        NavigationStack {
            Form {
                Section("Review") {
                    LabeledContent("Walls", value: "\(capturedRoom.walls.count)")
                    LabeledContent("Openings", value: "\(capturedRoom.doors.count + capturedRoom.windows.count)")
                    LabeledContent("Objects", value: "\(capturedRoom.objects.count)")
                }
                Section("Save room") {
                    TextField("Room name", text: $name)
                }
            }
            .navigationTitle("Review scan")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Discard") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { onSave(name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "Unnamed room" : name) }
                }
            }
        }
    }
}
