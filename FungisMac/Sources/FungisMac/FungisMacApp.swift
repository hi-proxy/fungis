import SwiftUI

@main
struct FungisMacApp: App {
    @StateObject private var model = AppModel()
    @AppStorage(BodyScale.key) private var bodyScale = BodyScale.normal

    var body: some Scene {
        WindowGroup("Fungis") {
            ContentView()
                .environmentObject(model)
                .frame(minWidth: 920, minHeight: 620)
                .task {
                    NotificationCoordinator.shared.start()
                    await model.run()
                }
        }
        .defaultSize(width: 1180, height: 760)
        .commands {
            CommandGroup(after: .sidebar) {
                Button("Refresh") { Task { await model.refresh() } }
                    .keyboardShortcut("r", modifiers: .command)
                Divider()
                Button("본문 크게") {
                    bodyScale = min(bodyScale + 1, BodyScale.last)
                }
                .keyboardShortcut("+", modifiers: .command)
                .disabled(bodyScale >= BodyScale.last)
                Button("본문 작게") { bodyScale = max(bodyScale - 1, 0) }
                    .keyboardShortcut("-", modifiers: .command)
                    .disabled(bodyScale <= 0)
                Button("본문 기본 크기") { bodyScale = BodyScale.normal }
                    .keyboardShortcut("0", modifiers: .command)
            }
        }
    }
}
