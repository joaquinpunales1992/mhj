def load_llm_model(load_local_model=True):
    from llama_cpp import Llama
    global llm_model
    if load_local_model:  
        llm_model = Llama(model_path="social/pretrained_llm_models/qwen2-0_5b-instruct-fp16.gguf")
    else:
        llm_model = Llama.from_pretrained(
            repo_id="Qwen/Qwen2-0.5B-Instruct-GGUF",
            filename="qwen2-0_5b-instruct-fp16.gguf",
            verbose=False,
            max_seq_len=512
        )