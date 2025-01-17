"""App Center crashes API wrappers."""

# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import datetime
import logging
import os
from typing import Any, Dict, Iterator, Optional
import urllib.parse

from azure.storage.blob import BlockBlobService
import deserialize

import appcenter.constants
from appcenter.derived_client import AppCenterDerivedClient, ProgressCallback
from appcenter.models import (
    ErrorGroup,
    ErrorGroups,
    ErrorGroupListItem,
    ErrorGroupState,
    HandledError,
    HandledErrors,
    HandledErrorDetails,
    SymbolType,
    SymbolUploadBeginResponse,
    SymbolUploadEndRequest,
    SymbolUploadStatus,
)


class AppCenterCrashesClient(AppCenterDerivedClient):
    """Wrapper around the App Center crashes APIs.

    :param token: The authentication token
    :param parent_logger: The parent logger that we will use for our own logging
    """

    def __init__(self, token: str, parent_logger: logging.Logger) -> None:
        super().__init__("crashes", token, parent_logger)

    def group_details(self, *, owner_name: str, app_name: str, error_group_id: str) -> ErrorGroup:
        """Get the error group details.

        :param str owner_name: The name of the app account owner
        :param str app_name: The name of the app
        :param str error_group_id: The ID of the error group to get the details for

        :returns: An ErrorGroup
        """

        request_url = self.generate_url(owner_name=owner_name, app_name=app_name)
        request_url += f"/errors/errorGroups/{error_group_id}"

        response = self.get(request_url)

        return deserialize.deserialize(ErrorGroup, response.json())

    def error_details(
        self, *, owner_name: str, app_name: str, error_group_id: str, error_id: str
    ) -> HandledErrorDetails:
        """Get the error details.

        :param str owner_name: The name of the app account owner
        :param str app_name: The name of the app
        :param str error_group_id: The ID of the error group to get the details for
        :param str error_id: The ID of the error to get the details for

        :returns: A HandledErrorDetails
        """

        request_url = self.generate_url(owner_name=owner_name, app_name=app_name)
        request_url += f"/errors/errorGroups/{error_group_id}/errors/{error_id}"

        response = self.get(request_url)

        return deserialize.deserialize(HandledErrorDetails, response.json())

    def error_info_dictionary(
        self, *, owner_name: str, app_name: str, error_group_id: str, error_id: str
    ) -> HandledErrorDetails:
        """Get the full error info dictionary.

        This is the full details that App Center has on a crash. It is not
        parsed due to being different per platform.

        :param str owner_name: The name of the app account owner
        :param str app_name: The name of the app
        :param str error_group_id: The ID of the error group to get the details for
        :param str error_id: The ID of the error to get the details for

        :returns: The raw full error info dictionary
        """

        request_url = self.generate_url(owner_name=owner_name, app_name=app_name)
        request_url += f"/errors/errorGroups/{error_group_id}/errors/{error_id}/download"

        response = self.get(request_url)

        return deserialize.deserialize(Dict[str, Any], response.json())

    def set_annotation(
        self,
        *,
        owner_name: str,
        app_name: str,
        error_group_id: str,
        annotation: str,
        state: Optional[ErrorGroupState] = None,
    ) -> None:
        """Get the error group details.

        :param str owner_name: The name of the app account owner
        :param str app_name: The name of the app
        :param str error_group_id: The ID of the error group to set the annotation on
        :param str annotation: The annotation text
        :param Optional[ErrorGroupState] state: The state to set the error group to

        The `state` parameter here does seem somewhat unusual, but it can't be
        helped unfortunately. The API requires that we set the state with the
        annotation. In order to work around this, the state can either be set
        explicitly in this call, or if it is set to `None` (the default), we'll
        read the existing state and use that.
        """

        if state is None:
            request_url = self.generate_url(owner_name=owner_name, app_name=app_name)
            request_url += f"/errors/errorGroups/{error_group_id}"

            response = self.get(request_url)

            group = deserialize.deserialize(ErrorGroup, response.json())
            state = group.state

        request_url = self.generate_url(owner_name=owner_name, app_name=app_name)
        request_url += f"/errors/errorGroups/{error_group_id}"

        self.patch(request_url, data={"state": state.value, "annotation": annotation})

    def get_error_groups(
        self,
        *,
        owner_name: str,
        app_name: str,
        start_time: datetime.datetime,
        end_time: Optional[datetime.datetime] = None,
        version: Optional[str] = None,
        app_build: Optional[str] = None,
        group_state: Optional[ErrorGroupState] = None,
        error_type: Optional[str] = None,
        order_by: Optional[str] = None,
        limit: int = 30,
    ) -> Iterator[ErrorGroupListItem]:
        """Get the error groups for an app.

        :param str owner_name: The name of the app account owner
        :param str app_name: The name of the app
        :param datetime.datetime start_time: The time to start getting error groups from
        :param Optional[datetime.datetime] end_time: The end time to get error groups from
        :param Optional[str] version: The version of the app to restrict the search to (if any)
        :param Optional[str] app_build: The build to restrict the search to (if any)
        :param Optional[ErrorGroupState] group_state: Set to filter to just this group state (open, closed, ignored)
        :param Optional[str] error_type: Set to filter to specific types of error (all, unhandledError, handledError)
        :param Optional[str] order_by: The order by parameter to pass in (this will be encoded for you)
        :param Optional[str] limit: The max number of results to return per request (should not go past 100)

        :returns: An iterator of ErrorGroupListItem
        """

        # pylint: disable=too-many-locals

        request_url = self.generate_url(owner_name=owner_name, app_name=app_name)
        request_url += "/errors/errorGroups?"

        parameters = {"start": start_time.replace(microsecond=0).isoformat()}

        if end_time:
            parameters["end"] = end_time.replace(microsecond=0).isoformat()

        if version:
            parameters["version"] = version

        if app_build:
            parameters["app_build"] = app_build

        if group_state:
            parameters["groupState"] = group_state.value

        if error_type:
            parameters["errorType"] = error_type

        if order_by:
            parameters["$orderby"] = order_by

        if limit:
            parameters["$top"] = str(limit)

        request_url += urllib.parse.urlencode(parameters)

        page = 0

        while True:

            page += 1

            self.log.info(f"Fetching page {page} of crash groups")

            response = self.get(request_url)

            error_groups = deserialize.deserialize(ErrorGroups, response.json())

            yield from error_groups.errorGroups

            if error_groups.nextLink is None:
                break

            request_url = appcenter.constants.API_BASE_URL + self._next_link_polished(
                error_groups.nextLink, owner_name, app_name
            )

        # pylint: disable=too-many-locals

    def errors_in_group(
        self,
        *,
        owner_name: str,
        app_name: str,
        error_group_id: str,
        start_time: Optional[datetime.datetime] = None,
        end_time: Optional[datetime.datetime] = None,
        model: Optional[str] = None,
        operating_system: Optional[str] = None,
    ) -> Iterator[HandledError]:
        """Get the errors in a group.

        :param str owner_name: The name of the app account owner
        :param str app_name: The name of the app
        :param str error_group_id: The ID of the group to get the errors from
        :param Optional[datetime.datetime] start_time: The time to start getting error groups from
        :param Optional[datetime.datetime] end_time: The end time to get error groups from
        :param Optional[str] model: The device model to restrict the search to (if any)
        :param Optional[str] operating_system: The OS to restrict the search to (if any)

        :returns: An iterator of HandledError
        """

        request_url = self.generate_url(owner_name=owner_name, app_name=app_name)
        request_url += f"/errors/errorGroups/{error_group_id}/errors?"

        parameters: Dict[str, str] = {}

        if start_time:
            parameters["start"] = start_time.replace(microsecond=0).isoformat()

        if end_time:
            parameters["end"] = end_time.replace(microsecond=0).isoformat()

        if model:
            parameters["model"] = model

        if operating_system:
            parameters["os"] = operating_system

        request_url += urllib.parse.urlencode(parameters)

        page = 0

        while True:

            page += 1

            self.log.info(f"Fetching page {page} of crashes for group {error_group_id}")

            response = self.get(request_url)

            errors = deserialize.deserialize(HandledErrors, response.json())

            yield from errors.errors

            if errors.nextLink is None:
                break

            request_url = appcenter.constants.API_BASE_URL + self._next_link_polished(
                errors.nextLink, owner_name, app_name
            )

    def begin_symbol_upload(
        self,
        *,
        owner_name: str,
        app_name: str,
        symbols_name: str,
        symbol_type: SymbolType,
        build_number: Optional[str] = None,
        version: Optional[str] = None,
    ) -> SymbolUploadBeginResponse:
        """Upload debug symbols

        :param str owner_name: The name of the app account owner
        :param str app_name: The name of the app
        :param str symbols_path: The path to the symbols
        :param str symbol_type: The type of symbols being uploaded
        :param Optional[str] build_number: The build number (required for Android)
        :param Optional[str] version: The build version (required for Android)

        :raises ValueError: If the build number or version aren't specified and it's an Android upload

        :returns: SymbolUploadBeginResponse
        """

        if symbol_type == SymbolType.PROGUARD:
            if build_number is None:
                raise ValueError("The build number is required for Android")

            if version is None:
                raise ValueError("The version is required for Android")

        request_url = self.generate_url(owner_name=owner_name, app_name=app_name)
        request_url += "/symbol_uploads"

        data = {"symbol_type": symbol_type.value, "file_name": symbols_name}

        if build_number:
            data["build"] = build_number

        if version:
            data["version"] = version

        response = self.post(request_url, data=data)

        return deserialize.deserialize(SymbolUploadBeginResponse, response.json())

    def commit_symbol_upload(
        self, *, owner_name: str, app_name: str, upload_id: str
    ) -> SymbolUploadEndRequest:
        """Commit a symbol upload operation

        :param str owner_name: The name of the app account owner
        :param str app_name: The name of the app
        :param str upload_id: The ID of the symbols upload to commit

        :returns: The App Center symbol upload end response
        """

        request_url = self.generate_url(owner_name=owner_name, app_name=app_name)
        request_url += f"/symbol_uploads/{upload_id}"

        data = {"status": "committed"}

        response = self.patch(request_url, data=data)

        return deserialize.deserialize(SymbolUploadEndRequest, response.json())

    def upload_symbols(
        self,
        *,
        owner_name: str,
        app_name: str,
        symbols_path: str,
        symbols_name: Optional[str] = None,
        symbol_type: SymbolType,
        build_number: Optional[str] = None,
        version: Optional[str] = None,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> None:
        """Upload debug symbols

        :param str owner_name: The name of the app account owner
        :param str app_name: The name of the app
        :param str symbols_path: The path to the symbols
        :param str symbols_name: The name to use for the symbols (defaults to file basename)
        :param str symbol_type: The type of symbols being uploaded
        :param Optional[str] build_number: The build number (required for Android)
        :param Optional[str] version: The build version (required for Android)
        :param Optional[ProgressCallback] progress_callback: The upload progress callback

        For the upload progress callback, this is a callable where the first
        parameter is the number of bytes uploaded, and the second parameter is
        the total number of bytes to upload (if known).

        :raises Exception: If something goes wrong
        :raises FileNotFoundError: If the symbols path doesn't have a file at the end
        """

        if not os.path.isfile(symbols_path):
            raise FileNotFoundError(f"There was no file at: {symbols_path}")

        if not symbols_name:
            symbols_name = os.path.basename(symbols_path)

        begin_upload_response = self.begin_symbol_upload(
            owner_name=owner_name,
            app_name=app_name,
            symbols_name=symbols_name,
            symbol_type=symbol_type,
            build_number=build_number,
            version=version,
        )

        with open(symbols_path, "rb") as symbols_file:
            url_components = urllib.parse.urlparse(begin_upload_response.upload_url)
            path = url_components.path[1:]
            container, blob_name = path.split("/")
            connection_string = f"BlobEndpoint={url_components.scheme}://{url_components.netloc};"
            connection_string += f"SharedAccessSignature={url_components.query}"
            service = BlockBlobService(connection_string=connection_string)
            service.create_blob_from_stream(
                container, blob_name, symbols_file, progress_callback=progress_callback
            )

        commit_response = self.commit_symbol_upload(
            owner_name=owner_name,
            app_name=app_name,
            upload_id=begin_upload_response.symbol_upload_id,
        )

        if commit_response.status != SymbolUploadStatus.COMMITTED:
            raise Exception("Failed to upload symbols")

    def _next_link_polished(self, next_link: str, owner_name: str, app_name: str) -> str:
        """Polish nextLink string gotten from AppCenter service

        :param str next_link: The nextLink property from a service response when items are queried in batches
        :param str owner_name: The name of the app account owner
        :param str app_name: The name of the app

        :returns: A polished next link to use on next batch
        """

        _ = self

        if f"{owner_name}/{app_name}" not in next_link:
            # For some apps, AppCenter is returning invalid nextLinks without app name and owner, just a // instead.
            next_link = next_link.replace("//", f"/{owner_name}/{app_name}/", 1)

        # AppCenter is returning a nextLink with a /api on the URL which causes the request to fail.
        return next_link.replace("/api", "", 1)
