import Foundation
import Testing
@testable import FungisMac

@Test func findsFileAndLineTheWayTheSecretaryWritesIt() {
    // 비서에게 코드를 인용하지 말고 이 형식으로 쓰라고 정했다.
    let found = CodeReference.found(in: """
    fungis_node/inbox.py:68
    읽을 것이 없을 때 깨우기를 안 지운다

    FungisMac/Sources/FungisMac/ChatView.swift:734-742
    같은 초안이 두 번 나간다
    """)
    #expect(found.count == 2)
    #expect(found[0].label == "fungis_node/inbox.py:68")
    #expect(found[0].firstLine == 68 && found[0].lastLine == 68)
    #expect(found[1].label == "FungisMac/Sources/FungisMac/ChatView.swift:734-742")
    #expect(found[1].lastLine == 742)
}

@Test func doesNotTurnEverydayNumbersIntoFiles() {
    // 확장자를 요구하지 않으면 방 번호와 시각이 전부 파일로 잡힌다.
    // 타임라인이 눌리지도 않는 단추로 뒤덮인다.
    #expect(CodeReference.found(in: "fungis reply 42 로 답한다").isEmpty)
    #expect(CodeReference.found(in: "#397:1 을 보라").isEmpty)
    #expect(CodeReference.found(in: "15:44:49 에 도착했다").isEmpty)
    #expect(CodeReference.found(in: "커밋 1294aa1 을 봐라").isEmpty)
}

@Test func theSameSpotIsListedOnce() {
    let found = CodeReference.found(in: "db.py:601 과 db.py:601 은 같은 자리다")
    #expect(found.count == 1)
}

@Test func aBackwardRangeDoesNotInvert() {
    // 잘못 적어도 첫 줄보다 앞으로 가지 않는다.
    let found = CodeReference.found(in: "web.py:100-20")
    #expect(found.first?.firstLine == 100)
    #expect(found.first?.lastLine == 100)
}
