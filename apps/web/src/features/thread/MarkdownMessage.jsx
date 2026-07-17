import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
import remarkGfm from "remark-gfm";

function withCitationLinks(content = "") {
  return String(content).replace(/\[(S\d+)\](?!\()/g, "[$1](eai-source:$1)");
}

export function MarkdownMessage({ content = "", citations = [], onCitation }) {
  const citationMap = new Map(citations.map((item) => [item.id, item]));
  return (
    <div className="message-markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        urlTransform={(url) => url.startsWith("eai-source:") ? url : defaultUrlTransform(url)}
        components={{
          a({ href = "", children }) {
            if (href.startsWith("eai-source:")) {
              const id = href.slice("eai-source:".length);
              const citation = citationMap.get(id);
              return (
                <button
                  type="button"
                  className="inline-citation"
                  title={citation?.title || id}
                  onClick={() => citation && onCitation?.(citation)}
                >
                  {children}
                </button>
              );
            }
            return <a href={href} target="_blank" rel="noreferrer">{children}</a>;
          }
        }}
      >
        {withCitationLinks(content)}
      </ReactMarkdown>
    </div>
  );
}
