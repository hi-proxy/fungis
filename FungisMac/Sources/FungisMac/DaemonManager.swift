import Foundation

enum DaemonError: LocalizedError {
    case executableNotFound
    case startupFailed(String)
    case foreignDaemonNotSending

    var errorDescription: String? {
        switch self {
        case .executableNotFound:
            "Fungis daemon executable was not found"
        case .startupFailed(let reason) where !reason.isEmpty:
            "Fungis daemon did not become ready — \(reason)"
        case .startupFailed:
            "Fungis daemon did not become ready"
        case .foreignDaemonNotSending:
            """
            다른 daemon이 127.0.0.1:8790을 쓰고 있고 그것은 깨우기를 보내지 않는다. \
            메시지는 저장되지만 터미널은 깨어나지 않는다. 그 프로세스를 끝내고 앱을 \
            다시 열어라.
            """
        }
    }
}

actor DaemonManager {
    static let shared = DaemonManager()

    private var process: Process?
    /// daemon이 죽으면서 남긴 말. 버리면 화면에 이유 없는 실패만 남는다.
    private var errorPipe: Pipe?
    private let healthURL = URL(string: "http://127.0.0.1:8790/health")!

    func ensureRunning() async throws {
        switch await health() {
        case .sending:
            return
        case .notSending:
            // 여기서 조용히 돌아가면 앱은 깨우기를 안 보내는 daemon을 자기 것으로
            // 삼고 아무 말도 하지 않는다. 초록불인데 메시지가 안 오는 상태다.
            throw DaemonError.foreignDaemonNotSending
        case .down:
            break
        }
        if let process, process.isRunning {
            try await waitUntilHealthy()
            return
        }
        let executable = try daemonExecutable()
        let child = Process()
        child.executableURL = executable
        child.arguments = ["daemon", "--send"]
        child.currentDirectoryURL = projectDirectory(for: executable)
        let errors = Pipe()
        child.standardOutput = FileHandle.nullDevice
        child.standardError = errors
        try child.run()
        process = child
        errorPipe = errors
        try await waitUntilHealthy()
    }

    /// 이미 죽은 프로세스의 파이프만 읽는다. 살아 있는 동안 읽으면 daemon이
    /// 로그를 쏟는 속도에 맞춰 여기가 멈춘다.
    private func startupFailure() -> String {
        guard let process, !process.isRunning, let errorPipe else { return "" }
        let data = errorPipe.fileHandleForReading.availableData
        let text = String(decoding: data, as: UTF8.self)
        return text.split(separator: "\n").last.map(String.init)?
            .trimmingCharacters(in: .whitespaces) ?? ""
    }

    enum Health {
        case down
        case sending
        case notSending
    }

    /// 200이라는 사실만으로는 부족하다. 깨우기를 보내는 daemon인지까지 본다.
    /// `sends_wakes`가 아예 없으면 서버가 앱보다 낡은 것이므로 받아준다.
    /// 칸 하나 없다고 앱이 안 뜨는 쪽이 더 나쁘다.
    private func health() async -> Health {
        var request = URLRequest(url: healthURL)
        request.timeoutInterval = 1
        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard (response as? HTTPURLResponse)?.statusCode == 200 else { return .down }
            let body = try? JSONDecoder().decode(HealthBody.self, from: data)
            return (body?.sendsWakes ?? true) ? .sending : .notSending
        } catch {
            return .down
        }
    }

    private struct HealthBody: Decodable {
        let sendsWakes: Bool?

        enum CodingKeys: String, CodingKey {
            case sendsWakes = "sends_wakes"
        }
    }

    private func waitUntilHealthy() async throws {
        for _ in 0..<50 {
            if await health() == .sending { return }
            if let process, !process.isRunning { break }
            try await Task.sleep(for: .milliseconds(200))
        }
        throw DaemonError.startupFailed(startupFailure())
    }

    private func daemonExecutable() throws -> URL {
        let environment = ProcessInfo.processInfo.environment
        if let override = environment["FUNGIS_DAEMON_EXECUTABLE"] {
            let url = URL(fileURLWithPath: override)
            if FileManager.default.isExecutableFile(atPath: url.path) { return url }
        }
        let candidates = [
            Bundle.main.bundleURL
                .deletingLastPathComponent()
                .deletingLastPathComponent()
                .deletingLastPathComponent()
                .appending(path: ".venv/bin/fungis-node"),
            URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
                .appending(path: ".venv/bin/fungis-node"),
        ]
        guard let found = candidates.first(where: {
            FileManager.default.isExecutableFile(atPath: $0.path)
        }) else {
            throw DaemonError.executableNotFound
        }
        return found
    }

    private func projectDirectory(for executable: URL) -> URL {
        executable
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
    }
}
