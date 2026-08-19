import SwiftUI

/// HQ의 구성원은 역할이 아니라 소집된 방이다. 그래서 프로젝트 방의
/// "Add role" 자리에 이것이 온다. 보드 모달 안에 있으면 노드를 보러 간
/// 사람이 명부를 만나고, 명부를 고치러 간 사람이 노드를 지나야 한다.
struct ConveneSheet: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("소집").font(.title2.bold())
                Spacer()
                Button("닫기") { dismiss() }
            }
            .padding(16)
            Divider()
            ScrollView {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(candidates) { candidate in
                        candidateRow(candidate)
                    }
                    if let message = model.errorMessage {
                        Text(message).font(.caption).foregroundStyle(.orange)
                    }
                }
                .padding(20)
            }
        }
        .frame(minWidth: 520, minHeight: 420)
        .task { await model.refreshBoard() }
        // lead 지정·해제 안내는 닫힐 때 한 번에 나간다. 여기서 세운 것과
        // 열리기 전 상태의 차이만 서버가 계산해 보낸다 — 모달 안에서 A를
        // 세웠다 B로 바꿨으면 A에게는 아무것도 가지 않는다.
        .onDisappear { Task { await model.flushLeadAnnouncements() } }
    }

    private var candidates: [BoardCandidate] { model.board.candidates ?? [] }
    private func candidateRow(_ candidate: BoardCandidate) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 10) {
                Toggle("", isOn: Binding(
                    get: { candidate.connected },
                    set: { on in
                        Task {
                            _ = on
                                ? await model.connectTrack(projectID: candidate.id)
                                : await model.disconnectTrack(projectID: candidate.id)
                        }
                    }
                ))
                .labelsHidden()
                .disabled(!candidate.canJoin && !candidate.connected)

                Text(candidate.name)
                Spacer()
                if let lead = candidate.lead {
                    Text("lead · \(lead.name)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else {
                    Text("lead 없음")
                        .font(.caption)
                        .foregroundStyle(.orange)
                }
            }
            // 여기서 바로 세우고 바꾼다. 딴 화면으로 보내면 소집하다 말고
            // 갔다가 돌아와서 다시 골라야 한다. 세운 뒤에 숨기면 세울 수는
            // 있는데 바꿀 수는 없는 화면이 된다.
            leadPicker(candidate)
        }
        .padding(.vertical, 2)
    }

    /// 고를 게 하나뿐일 때만 문장으로 시킨다. 이미 세워져 있으면 이름만 보이고
    /// 나머지가 갈아탈 자리가 된다.
    private func leadChipLabel(
        _ candidate: BoardCandidate, role: BoardRole, isLead: Bool
    ) -> String {
        if isLead { return role.name }
        if candidate.lead != nil { return role.name }
        return candidate.roles.count == 1 ? "\(role.name)을 lead로" : role.name
    }

    @ViewBuilder
    private func leadPicker(_ candidate: BoardCandidate) -> some View {
        if candidate.roles.isEmpty {
            Text("배정된 역할이 없어 lead를 세울 수 없다")
                .font(.caption)
                .foregroundStyle(.secondary)
                .padding(.leading, 28)
        } else {
            HStack(spacing: 6) {
                ForEach(candidate.roles) { role in
                    let isLead = role.id == candidate.lead?.id
                    Button(leadChipLabel(candidate, role: role, isLead: isLead)) {
                        // 이미 lead인 칩은 누를 일이 없다. 눌러도 같은 자리다.
                        guard !isLead, !model.isMutating else { return }
                        Task { _ = await model.setRoleLead(roleID: role.id, isLead: true) }
                    }
                    .buttonStyle(.plain)
                    // hit-area: 도형은 여백 바깥에 준다. 여백 안쪽에 주면
                    // 캡슐은 큰데 글자만 눌리는 칩이 된다.
                    .font(.caption)
                    .foregroundStyle(isLead ? AnyShapeStyle(.primary) : AnyShapeStyle(.secondary))
                    .padding(.horizontal, 8)
                    .padding(.vertical, 3)
                    .background(
                        isLead ? AnyShapeStyle(.tint.opacity(0.25))
                               : AnyShapeStyle(.quaternary.opacity(0.4)),
                        in: Capsule()
                    )
                    .contentShape(Capsule())
                    // 저장중 표시. lead를 세우는 동안 칩 전부가 흐려지고
                    // 눌리지 않는다. 취소는 없다 — 닫는 것이 곧 확정이다.
                    .opacity(model.isMutating ? 0.4 : 1)
                    .disabled(isLead || model.isMutating)
                }
            }
            .padding(.leading, 28)
        }
    }}
