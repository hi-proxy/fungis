import SwiftUI

/// 비서가 짚어 준 자리를 보는 창. 읽기 전용이다.
///
/// IDE 로 가지 않는다 — 편집도 검색도 git 통합도 없다. 목적은 코멘트에 적힌
/// 줄을 눈으로 확인하는 것 하나다. 그래야 비서가 코드를 메시지 본문에 베끼지
/// 않아도 되고, 그쪽이 토큰을 훨씬 아낀다.
struct CodeSheet: View {
    let reference: CodeReference
    let projectID: String
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var file: RepositoryFile?
    @State private var failure: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text(reference.label).font(.headline).textSelection(.enabled)
                if let file, file.truncated {
                    Text("앞 \(file.lines.count)줄만 · 전체 \(file.totalLines)줄")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Button("닫기") { dismiss() }
            }
            .padding(14)
            Divider()
            content
        }
        .frame(width: 900, height: 620)
        .task { await load() }
    }

    @ViewBuilder private var content: some View {
        if let failure {
            VStack(spacing: 10) {
                Image(systemName: "doc.questionmark").font(.largeTitle)
                Text(failure).font(.callout).multilineTextAlignment(.center)
                    .foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity).padding(30)
        } else if let file {
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 0) {
                        ForEach(Array(file.lines.enumerated()), id: \.offset) { index, text in
                            line(number: index + 1, text: text)
                        }
                    }.padding(.vertical, 8)
                }
                .onAppear {
                    // 짚어 준 자리를 화면 가운데로. 맨 위에 붙이면 앞뒤
                    // 맥락이 안 보여서 무엇을 고치라는 것인지 알 수 없다.
                    proxy.scrollTo(reference.firstLine, anchor: .center)
                }
            }
        } else {
            ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    private func line(number: Int, text: String) -> some View {
        let marked = number >= reference.firstLine && number <= reference.lastLine
        return HStack(alignment: .top, spacing: 10) {
            Text("\(number)")
                .font(.system(.caption, design: .monospaced))
                .foregroundStyle(.tertiary)
                .frame(width: 52, alignment: .trailing)
            Text(text.isEmpty ? " " : text)
                .font(.system(.callout, design: .monospaced))
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.horizontal, 12).padding(.vertical, 1)
        .background(marked ? Color.yellow.opacity(0.18) : .clear)
        .id(number)
    }

    private func load() async {
        do {
            file = try await model.api.file(projectID: projectID, path: reference.path)
        } catch {
            failure = error.localizedDescription
        }
    }
}

/// 메시지에 딸린 코드 자리 단추들. 없으면 아무것도 안 그린다.
///
/// MessageRow 본문에 인라인으로 두면 그 뷰의 타입 검사가 한계를 넘는다.
/// 별도 뷰로 떼는 것이 여기서는 취향이 아니라 컴파일 조건이다.
struct CodeReferenceRow: View {
    let references: [CodeReference]
    let open: (CodeReference) -> Void

    var body: some View {
        if !references.isEmpty {
            HStack(spacing: 6) {
                ForEach(references) { reference in
                    Button { open(reference) } label: {
                        Label(
                            reference.label,
                            systemImage: "chevron.left.forwardslash.chevron.right"
                        )
                        .font(.caption2).lineLimit(1)
                        .padding(.horizontal, 7).padding(.vertical, 3)
                        .background(.quaternary.opacity(0.5), in: Capsule())
                    }
                    .buttonStyle(.plain)
                    .help("이 자리를 연다")
                }
            }
        }
    }
}
