EMAIL = ""
PASSWD = ""


from langchain_huggingface import ChatHuggingFace
from langchain_huggingface import HuggingFaceEndpoint

llm = HuggingFaceEndpoint(
    endpoint_url="https://api-inference.huggingface.co/models/meta-llama/Llama-3.3-70B-Instruct",
    max_new_tokens=512,
    top_p=0.95,
    temperature=0.6,
    huggingfacehub_api_token=""
)

chat_model = ChatHuggingFace(llm=llm)

messages = [("human", "Create a caption for a real estate post for a house in tokyo, close to the beach that costs 20.000 usd ")]
response = chat_model.invoke(messages)
print(response.content)


