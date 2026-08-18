import Foundation
import Testing
@testable import FungisMac

private func node(_ id: String, waitsFor: [String] = []) throws -> BoardNode {
    let payload: [String: Any] = [
        "id": id,
        "project_id": "p",
        "title": id,
        "status": "todo",
        "state": waitsFor.isEmpty ? "todo" : "waiting",
        "blocked_by": waitsFor,
    ]
    let data = try JSONSerialization.data(withJSONObject: payload)
    return try JSONDecoder().decode(BoardNode.self, from: data)
}

@Test func aLinkThatWouldRepeatItselfIsRefusedBeforeItIsPressed() throws {
    let first = try node("a")
    let second = try node("b", waitsFor: ["a"])

    #expect(BoardGraph.refusal(source: first, target: second, nodes: [first, second])
        == "이미 이어져 있다")
    // 반대 방향은 아직 비어 있다. 막으면 안 된다.
    #expect(BoardGraph.refusal(source: second, target: first, nodes: [first, second])
        == "순환이 된다")
}

@Test func aLinkThatClosesALoopSeveralStepsAwayIsRefused() throws {
    // c가 b를 기다리고 b가 a를 기다린다. a를 c 뒤에 두면 고리가 닫힌다.
    let first = try node("a")
    let second = try node("b", waitsFor: ["a"])
    let third = try node("c", waitsFor: ["b"])
    let all = [first, second, third]

    #expect(BoardGraph.refusal(source: third, target: first, nodes: all) == "순환이 된다")
    // 한 단계도 못 보면 이 검사는 없는 것과 같다.
    #expect(BoardGraph.reaches(from: "c", to: "a", nodes: all))
}

@Test func anHonestLinkIsNotRefused() throws {
    let first = try node("a")
    let second = try node("b", waitsFor: ["a"])
    let unrelated = try node("z")
    let all = [first, second, unrelated]

    #expect(BoardGraph.refusal(source: second, target: unrelated, nodes: all) == nil)
    #expect(BoardGraph.refusal(source: unrelated, target: second, nodes: all) == nil)
}

@Test func aNodeCannotWaitForItself() throws {
    let only = try node("a")
    #expect(BoardGraph.refusal(source: only, target: only, nodes: [only]) == "자기 자신이다")
}

@Test func aTicketCannotStartWhileItsPredecessorIsUnfinished() throws {
    let blocker = try node("a")
    let waiting = try node("b", waitsFor: ["a"])
    let all = [blocker, waiting]

    #expect(BoardGraph.blocksStarting(waiting, to: "active", nodes: all)?.id == "a")
    // 안 시작으로 되돌리는 것은 막지 않는다. 되돌릴 길이 없으면 손이 갇힌다.
    #expect(BoardGraph.blocksStarting(waiting, to: "todo", nodes: all) == nil)
    // 이미 해버린 일을 못 했다고 우기면 사람이 보드를 버린다.
    #expect(BoardGraph.blocksStarting(waiting, to: "done", nodes: all) == nil)
}

@Test func aTicketStartsFreelyOnceEveryPredecessorIsDone() throws {
    let payload: [String: Any] = [
        "id": "a", "project_id": "p", "title": "a",
        "status": "done", "state": "done", "blocked_by": [],
    ]
    let finished = try JSONDecoder().decode(
        BoardNode.self, from: JSONSerialization.data(withJSONObject: payload)
    )
    let waiting = try node("b", waitsFor: ["a"])

    #expect(BoardGraph.blocksStarting(waiting, to: "active", nodes: [finished, waiting]) == nil)
}
