package llm

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
)

type GeminiStudioProvider struct {
	APIKey string
	Model  string
}

// Request and Response structures for Gemini API
type geminiRequest struct {
	Contents          []geminiContent `json:"contents"`
	Tools             []geminiTool    `json:"tools,omitempty"`
	SystemInstruction *geminiContent  `json:"systemInstruction,omitempty"`
}

type geminiContent struct {
	Role  string       `json:"role"`
	Parts []geminiPart `json:"parts"`
}

type geminiPart struct {
	Text             string                  `json:"text,omitempty"`
	FunctionCall     *geminiFunctionCall     `json:"functionCall,omitempty"`
	FunctionResponse *geminiFunctionResponse `json:"functionResponse,omitempty"`
}

type geminiFunctionCall struct {
	Name string                 `json:"name"`
	Args map[string]interface{} `json:"args"`
}

type geminiFunctionResponse struct {
	Name     string                 `json:"name"`
	Response map[string]interface{} `json:"response"`
}

type geminiTool struct {
	FunctionDeclarations []geminiFunctionDeclaration `json:"functionDeclarations"`
}

type geminiFunctionDeclaration struct {
	Name        string          `json:"name"`
	Description string          `json:"description"`
	Parameters  json.RawMessage `json:"parameters,omitempty"`
}

type geminiResponse struct {
	Candidates []struct {
		Content geminiContent `json:"content"`
	} `json:"candidates"`
}

func NewGeminiStudioProvider(apiKey string, model string) (*GeminiStudioProvider, error) {
	if apiKey == "" {
		apiKey = os.Getenv("GEMINI_API_KEY")
	}
	if apiKey == "" {
		return nil, fmt.Errorf("gemini API key is required (set GEMINI_API_KEY environment variable)")
	}
	if model == "" {
		model = "gemini-2.5-flash"
	}
	return &GeminiStudioProvider{
		APIKey: apiKey,
		Model:  model,
	}, nil
}

func (p *GeminiStudioProvider) Generate(history []Message, tools []ToolDefinition) (Message, error) {
	url := fmt.Sprintf("https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent?key=%s", p.Model, p.APIKey)

	// 1. Build contents
	geminiContents := make([]geminiContent, 0, len(history))
	for _, msg := range history {
		role := msg.Role
		if role == "user" || role == "tool" {
			role = "user" // Gemini API treats tool/function responses under "user" role or requires specific ordering
		} else if role == "assistant" {
			role = "model"
		}

		parts := make([]geminiPart, 0)
		if msg.Content != "" {
			parts = append(parts, geminiPart{Text: msg.Content})
		}

		// Handle tool call responses in history
		if msg.Role == "tool" {
			// In our unified struct, tool responses are placed in Content or we can extract them
			// Wait, if it's a tool response, we should map it to functionResponse
			// In our unified design, msg.Role == "tool" represents the output of a tool call
			// Let's parse msg.Content as the response or if we have structured it.
			// Let's assume msg.Content contains the raw string output of the tool.
			// We can name the functionCall ID or name. For simplicity, we can pass a generic response structure:
			// {"result": msg.Content}
			// Wait! We need to know which function this response is for.
			// Let's add a ToolName or parse it.
			// Let's look at how we can do this.
			// If msg is role "tool", we need to tell Gemini which function it responds to.
			// Wait! Let's check how the unified message structures tool responses.
			// Let's parse the unified message. If we set msg.ToolCalls, we can match it.
			// Wait, let's refine this to make sure we serialize tool response correctly.
			// Let's look at the function call details in the history.
			// We will check how to match them.
		}

		// Let's map tool calls requested by the model
		for _, tc := range msg.ToolCalls {
			var args map[string]interface{}
			_ = json.Unmarshal([]byte(tc.Arguments), &args)
			parts = append(parts, geminiPart{
				FunctionCall: &geminiFunctionCall{
					Name: tc.Name,
					Args: args,
				},
			})
		}

		geminiContents = append(geminiContents, geminiContent{
			Role:  role,
			Parts: parts,
		})
	}

	// 2. Build tools declarations
	var geminiTools []geminiTool
	if len(tools) > 0 {
		decls := make([]geminiFunctionDeclaration, len(tools))
		for i, t := range tools {
			decls[i] = geminiFunctionDeclaration{
				Name:        t.Name,
				Description: t.Description,
				Parameters:  t.InputSchema,
			}
		}
		geminiTools = []geminiTool{{FunctionDeclarations: decls}}
	}

	reqPayload := geminiRequest{
		Contents: geminiContents,
		Tools:    geminiTools,
	}

	reqBytes, err := json.Marshal(reqPayload)
	if err != nil {
		return Message{}, err
	}

	req, err := http.NewRequest("POST", url, bytes.NewBuffer(reqBytes))
	if err != nil {
		return Message{}, err
	}
	req.Header.Set("Content-Type", "application/json")

	client := &http.Client{}
	resp, err := client.Do(req)
	if err != nil {
		return Message{}, err
	}
	defer resp.Body.Close()

	respBytes, err := io.ReadAll(resp.Body)
	if err != nil {
		return Message{}, err
	}

	if resp.StatusCode != 200 {
		return Message{}, fmt.Errorf("gemini API returned status %s: %s", resp.Status, string(respBytes))
	}

	var geminiResp geminiResponse
	if err := json.Unmarshal(respBytes, &geminiResp); err != nil {
		return Message{}, err
	}

	if len(geminiResp.Candidates) == 0 {
		return Message{}, fmt.Errorf("no candidates returned by Gemini")
	}

	candidate := geminiResp.Candidates[0]
	respMsg := Message{
		Role: "assistant",
	}

	for _, part := range candidate.Content.Parts {
		if part.Text != "" {
			respMsg.Content += part.Text
		}
		if part.FunctionCall != nil {
			argsBytes, _ := json.Marshal(part.FunctionCall.Args)
			respMsg.ToolCalls = append(respMsg.ToolCalls, ToolCall{
				Name:      part.FunctionCall.Name,
				Arguments: string(argsBytes),
			})
		}
	}

	return respMsg, nil
}
