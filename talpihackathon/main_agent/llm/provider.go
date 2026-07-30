package llm

import (
	"encoding/json"
)

type Message struct {
	Role      string     `json:"role"`      // "user", "model" / "assistant", "tool"
	Content   string     `json:"content"`   // Text content
	ToolCalls []ToolCall `json:"toolCalls,omitempty"`
}

type ToolCall struct {
	ID        string `json:"id"`
	Name      string `json:"name"`
	Arguments string `json:"arguments"` // JSON-formatted string of arguments
}

type ToolDefinition struct {
	Name        string          `json:"name"`
	Description string          `json:"description"`
	InputSchema json.RawMessage `json:"inputSchema"` // JSON Schema of parameters
}

type Provider interface {
	// Generate content based on chat history and available tools
	Generate(history []Message, tools []ToolDefinition) (Message, error)
}
