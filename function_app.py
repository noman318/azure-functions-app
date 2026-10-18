import azure.functions as func
import logging

app = func.FunctionApp()

# @app.blob_trigger(arg_name="myblob", path="mycontainer/{name}",
#                                connection="a5f5f0_STORAGE") 
# def blob_trigger(myblob: func.InputStream) -> None:
#     logging.info("Blob trigger fired.")
#     logging.info("Blob name: %s", myblob.name)
#     logging.info("Blob size: %s bytes", myblob.length)

@app.blob_trigger(
    arg_name="myblob",
    path="mycontainer/{name}",
    connection="a5f5f0_STORAGE",
    source="EventGrid",
)
def blob_trigger(myblob: func.InputStream) -> None:
    logging.info("Blob trigger fired.")
    logging.info("Blob name: %s", myblob.name)
    logging.info("Blob size: %s bytes", myblob.length)


# This example uses SDK types to directly access the underlying BlobClient object provided by the Blob storage trigger.
# To use, uncomment the section below and add azurefunctions-extensions-bindings-blob to your requirements.txt file
# Ref: aka.ms/functions-sdk-blob-python
#
# import azurefunctions.extensions.bindings.blob as blob
# @app.blob_trigger(arg_name="client", path="mycontainer",
#                   connection="a5f5f0_STORAGE")
# def blob_trigger(client: blob.BlobClient):
#     logging.info(
#         f"Python blob trigger function processed blob \n"
#         f"Properties: {client.get_blob_properties()}\n"
#         f"Blob content head: {client.download_blob().read(size=1)}"
#     )
