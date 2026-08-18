import Foundation

enum DaemonError: LocalizedError {
    case executableNotFound
    case startupFailed(String)
    case foreignDaemonWontShutDown
    case staleAfterRestart

    var errorDescription: String? {
        switch self {
        case .executableNotFound:
            "Fungis daemon executable was not found"
        case .startupFailed(let reason) where !reason.isEmpty:
            "Fungis daemon did not become ready — \(reason)"
        case .startupFailed:
            "Fungis daemon did not become ready"
        case .foreignDaemonWontShutDown:
            """
            127.0.0.1:8790의 daemon이 낡았는데 내려가지 않는다. /shutdown이 없는 \
            아주 옛 daemon이거나 종료가 실패했다. 그 프로세스를 손으로 끝내고 앱을 \
            다시 열어라.
            """
        case .staleAfterRestart:
            """
            daemon을 새로 띄웠는데도 소스가 낡았다고 말한다. 파이썬 소스가 지금 \
            바뀌는 중이거나 지문 판정에 문제가 있다. 여기서 또 갈아치우면 무한 \
            재시작이 되므로 멈춘다. 소스가 안정된 뒤 앱을 다시 열어라.
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
    private let shutdownURL = URL(string: "http://127.0.0.1:8790/shutdown")!

    func ensureRunning() async throws {
        switch await health() {
        case .sending:
            return
        case .replaceable:
            // 낡은 daemon이다 — 소스가 바뀌었거나(stale), stale 칸조차 없는
            // 옛 빌드이거나, 깨우기를 안 보낸다. 앱이 daemon을 소유하므로
            // 내리고 새로 띄우는 것이 정당하다. 예전에는 notSending을 에러로
            // 던지고 사람에게 죽이라고 시켰는데, 이제 앱이 그 일을 직접 한다.
            try await shutDownForeignDaemon()
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

    /// 낡은 daemon에게 /shutdown을 보내고 8790이 비워질 때까지 기다린다.
    /// pid 없이 내리는 길이며, daemon이 자식 서버까지 정리하고 내려간다.
    /// 갈아치우기는 ensureRunning 한 번에 한 번뿐이다 — 새로 띄운 것이 또
    /// 낡았다고 하면 waitUntilHealthy가 staleAfterRestart로 멈춘다.
    private func shutDownForeignDaemon() async throws {
        var request = URLRequest(url: shutdownURL)
        request.httpMethod = "POST"
        request.timeoutInterval = 2
        guard let (_, response) = try? await URLSession.shared.data(for: request),
              (response as? HTTPURLResponse)?.statusCode == 200 else {
            throw DaemonError.foreignDaemonWontShutDown
        }
        for _ in 0..<25 {
            if await health() == .down {
                // 내린 daemon이 우리 자식이었더라도 이제 우리 것이 아니다.
                // 남겨두면 아래 isRunning 판정이 죽어가는 프로세스를 붙잡는다.
                process = nil
                errorPipe = nil
                return
            }
            try await Task.sleep(for: .milliseconds(200))
        }
        throw DaemonError.foreignDaemonWontShutDown
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

    enum Health: Equatable {
        case down
        /// 지금 코드로 돌고 깨우기도 보낸다. 채택한다.
        case sending
        /// 살아는 있으나 자기 것으로 삼으면 안 된다. 내리고 새로 띄운다.
        case replaceable
    }

    /// 200이라는 사실만으로는 부족하다. 지금 코드로 도는지, 깨우기를 보내는지까지
    /// 본다. `stale` 칸이 없으면 이 판정이 생기기 전의 낡은 빌드이므로 그것도
    /// 교체 대상이다 — sends_wakes 유무로 세대를 더 가르지 않는다.
    static func verdict(statusCode: Int?, body: Data) -> Health {
        guard statusCode == 200 else { return .down }
        guard let parsed = try? JSONDecoder().decode(HealthBody.self, from: body),
              let stale = parsed.stale else {
            return .replaceable
        }
        if stale { return .replaceable }
        return (parsed.sendsWakes ?? true) ? .sending : .replaceable
    }

    private func health() async -> Health {
        var request = URLRequest(url: healthURL)
        request.timeoutInterval = 1
        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            return Self.verdict(
                statusCode: (response as? HTTPURLResponse)?.statusCode, body: data
            )
        } catch {
            return .down
        }
    }

    struct HealthBody: Decodable {
        let sendsWakes: Bool?
        let stale: Bool?

        enum CodingKeys: String, CodingKey {
            case sendsWakes = "sends_wakes"
            case stale
        }
    }

    private func waitUntilHealthy() async throws {
        for _ in 0..<50 {
            switch await health() {
            case .sending:
                return
            case .replaceable:
                // 우리가 방금 띄운 daemon이 또 교체 대상이다. 여기서 다시
                // 갈아치우면 무한 재시작이므로 이유를 말하고 멈춘다.
                throw DaemonError.staleAfterRestart
            case .down:
                break
            }
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
