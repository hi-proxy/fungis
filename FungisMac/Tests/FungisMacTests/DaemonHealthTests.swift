import Foundation
import Testing
@testable import FungisMac

/// 채택 판정만 순수 함수로 떼어 붙잡는다. 실 프로세스를 띄우는 나머지는
/// mock하지 않는다 — 사람이 앱을 열어 확인하는 것까지가 이 기능의 검증이다.
private func verdict(_ status: Int?, _ json: String) -> DaemonManager.Health {
    DaemonManager.verdict(statusCode: status, body: Data(json.utf8))
}

@Test func adoptsOnlyAFreshSendingDaemon() {
    #expect(verdict(200, #"{"status":"ok","sends_wakes":true,"stale":false}"#) == .sending)
}

@Test func staleDaemonGetsReplaced() {
    #expect(verdict(200, #"{"status":"ok","sends_wakes":true,"stale":true}"#) == .replaceable)
}

@Test func healthWithoutStaleFieldIsAnOldBuildAndGetsReplaced() {
    // stale 칸이 없으면 이 판정이 생기기 전의 daemon이다. sends_wakes 유무로
    // 세대를 더 가르지 않는다 — 없으면 무조건 교체다.
    #expect(verdict(200, #"{"status":"ok","sends_wakes":true}"#) == .replaceable)
    #expect(verdict(200, #"{"status":"ok"}"#) == .replaceable)
    #expect(verdict(200, "not json") == .replaceable)
}

@Test func dryRunDaemonGetsReplacedInsteadOfErroring() {
    // 예전에는 에러를 던지고 사람에게 죽이라고 시켰다. 이제 앱이 직접 간다.
    #expect(verdict(200, #"{"status":"ok","sends_wakes":false,"stale":false}"#) == .replaceable)
}

@Test func nonTwoHundredIsDown() {
    #expect(verdict(nil, "") == .down)
    #expect(verdict(500, #"{"status":"ok","sends_wakes":true,"stale":false}"#) == .down)
}
